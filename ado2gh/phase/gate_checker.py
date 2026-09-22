"""Phase gate checker — validates success thresholds before advancing."""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from ado2gh.logging_config import log
from ado2gh.models import (
    DEFAULT_PHASES,
    GateStatus,
    PhaseConfig,
    PhaseGateResult,
    PhaseType,
)

if TYPE_CHECKING:

    class _GateStateDB(Protocol):
        """Attributes ``PhaseGateChecker`` expects from a state DB backend.

        Both concrete backends (``SQLiteStateDB``, ``PostgresStateDB``) implement
        these; ``_conn`` is a private per-backend connection context manager not
        declared on the shared ``StateDBBase`` ABC, so it is named here instead.

        ``_conn`` is deliberately wider than the sibling Protocols in
        ``ado2gh/state/``: those describe one backend's own mixin host, so they
        can name that backend's exact type (``sqlite3.Connection`` for the
        SQLite mixins, the generator signature for the Postgres ones). This one
        spans both, because ``PhaseGateChecker`` is constructed from
        ``create_state_db()``, whose ``StateStore`` is the union of the two.
        ``SQLiteStateDB._conn`` returns a bare ``sqlite3.Connection`` and
        ``PostgresStateDB._conn`` is a ``@contextmanager``; the one call pattern
        here, ``with self.db._conn() as conn``, is what both satisfy, and
        ``AbstractContextManager`` is the only declaration that covers them
        both. Narrowing it to ``sqlite3.Connection`` was measured against mypy
        and fails: `Argument 1 to "PhaseGateChecker" has incompatible type
        "SQLiteStateDB | PostgresStateDB"` at ``ado2gh/api/accelerator.py``.
        """

        def _conn(self) -> AbstractContextManager[Any]: ...

        def get_risk_scores_for_phase(self, phase: PhaseType | str | None) -> list[dict]: ...

        def upsert_phase_gate(self, result: PhaseGateResult) -> None: ...

        def get_phase_gate(self, phase: PhaseType) -> dict | None: ...
else:
    # Parity with every other Protocol host in the package: without it the
    # annotation would raise NameError under typing.get_type_hints at runtime.
    _GateStateDB = object


class PhaseGateChecker:
    """Evaluate a phase's success gate and record the result, or an operator override.

    ``check`` is the pure evaluation; ``override`` is the only path that
    persists an ``OVERRIDE`` status, and it always carries the operator's
    reason so the audit trail names why the gate was forced (CA-002).
    """

    def __init__(self, db: _GateStateDB, phase_configs: dict[PhaseType, PhaseConfig] | None = None) -> None:
        """Bind the state DB and the per-phase thresholds.

        Args:
            db: State store holding risk scores, migrations and gate results.
            phase_configs: Thresholds per phase; defaults to ``DEFAULT_PHASES``.
        """
        self.db = db
        self.phases = phase_configs or DEFAULT_PHASES

    def check(self, phase: PhaseType) -> PhaseGateResult:
        """Measure the phase's repo and pipeline success against its thresholds.

        The result is persisted with ``PASS`` or ``FAIL``; a phase with no
        assigned repos fails with a hint to run ``phase assign``.

        Args:
            phase: The phase whose gate is evaluated.

        Returns:
            The gate result, including the failure reasons when it did not pass.
        """
        cfg = self.phases[phase]
        scores = self.db.get_risk_scores_for_phase(phase)
        if not scores:
            return PhaseGateResult(
                phase=phase, status=GateStatus.FAIL,
                repo_success_pct=0.0, pipeline_success_pct=0.0,
                repos_completed=0, repos_total=0,
                pipelines_completed=0, pipelines_total=0,
                failures=["No repos assigned. Run: phase assign"],
            )
        repo_names = [s["repo_name"] for s in scores]
        total_repos = len(repo_names)
        failures: list[str] = []

        with self.db._conn() as conn:
            rows = conn.execute(
                "SELECT ado_repo, COUNT(*) total_scopes, "
                "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) ok_scopes "
                "FROM migrations WHERE ado_repo IN ({}) GROUP BY ado_repo".format(
                    ",".join("?" * len(repo_names))
                ), repo_names,
            ).fetchall() if repo_names else []

        repos_done = sum(1 for r in rows
                         if r["ok_scopes"] == r["total_scopes"] and r["total_scopes"] > 0)
        repo_pct = repos_done / total_repos if total_repos else 0.0

        with self.db._conn() as conn:
            pr = conn.execute(
                "SELECT COUNT(*) total, "
                "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) done "
                "FROM pipeline_migrations WHERE repo_name IN ({})".format(
                    ",".join("?" * len(repo_names))
                ), repo_names,
            ).fetchone() if repo_names else None
        total_pipes = pr["total"] if pr else 0
        pipes_done = pr["done"] if pr else 0
        pipe_pct = pipes_done / total_pipes if total_pipes else 1.0

        # gate_min_completed is configured against the phase cap (for example PILOT=95),
        # so cap the required count at the actual phase population — otherwise a
        # phase with fewer repos than the cap could never pass.
        min_required = min(cfg.gate_min_completed, total_repos)
        if total_repos > 0 and repos_done < min_required:
            failures.append(f"repos_completed={repos_done} < min={min_required}")
        if repo_pct < cfg.gate_repo_success_pct:
            failures.append(
                f"repo_success={repo_pct:.1%} < threshold={cfg.gate_repo_success_pct:.0%}")
        if total_pipes > 0 and pipe_pct < cfg.gate_pipeline_success_pct:
            failures.append(
                f"pipeline_success={pipe_pct:.1%} < threshold={cfg.gate_pipeline_success_pct:.0%}")

        with self.db._conn() as conn:
            failed_repos = [dict(r)["ado_repo"] for r in conn.execute(
                "SELECT DISTINCT ado_repo FROM migrations "
                "WHERE ado_repo IN ({}) AND status='failed'".format(
                    ",".join("?" * len(repo_names))
                ), repo_names,
            ).fetchall()] if repo_names else []
        if failed_repos:
            failures.append(f"Failed repos ({len(failed_repos)}): {', '.join(failed_repos[:10])}")

        status = GateStatus.PASS if not failures else GateStatus.FAIL
        result = PhaseGateResult(
            phase=phase, status=status,
            repo_success_pct=repo_pct, pipeline_success_pct=pipe_pct,
            repos_completed=repos_done, repos_total=total_repos,
            pipelines_completed=pipes_done, pipelines_total=total_pipes,
            failures=failures, checked_at=datetime.now(timezone.utc).isoformat(),
        )
        self.db.upsert_phase_gate(result)
        return result

    def override(self, phase: PhaseType, reason: str) -> PhaseGateResult:
        """Evaluate the gate, then persist it as ``OVERRIDE`` with the operator's reason.

        Args:
            phase: The phase whose gate is being forced.
            reason: Why the operator accepts the failures. Mandatory and stored
                on the gate record for the audit trail (CA-002).

        Returns:
            The persisted gate result with status ``OVERRIDE``.
        """
        result = self.check(phase)
        result.status = GateStatus.OVERRIDE
        result.override_reason = reason
        self.db.upsert_phase_gate(result)
        log.warning(f"Gate {phase.value} OVERRIDDEN: {reason}")
        return result

    def can_advance(self, phase: PhaseType) -> bool:
        """Report whether the phase's recorded gate is ``PASS`` or ``OVERRIDE``.

        Args:
            phase: The phase whose stored gate result is consulted.

        Returns:
            True when a gate result exists and lets the next phase start.
        """
        gate = self.db.get_phase_gate(phase)
        return gate is not None and gate["status"] in (
            GateStatus.PASS.value, GateStatus.OVERRIDE.value)
