"""Phase gate checker — validates success thresholds before advancing."""
from __future__ import annotations

from datetime import datetime, timezone

from ado2gh.logging_config import log
from ado2gh.models import (
    DEFAULT_PHASES, GateStatus, PhaseGateResult, PhaseType,
)
from ado2gh.state.db import StateDB


class PhaseGateChecker:
    def __init__(self, db: StateDB, phase_configs: dict = None):
        self.db = db
        self.phases = phase_configs or DEFAULT_PHASES

    def check(self, phase: PhaseType) -> PhaseGateResult:
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

        if repos_done < cfg.gate_min_completed:
            failures.append(f"repos_completed={repos_done} < min={cfg.gate_min_completed}")
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
        result = self.check(phase)
        result.status = GateStatus.OVERRIDE
        result.override_reason = reason
        self.db.upsert_phase_gate(result)
        log.warning(f"Gate {phase.value} OVERRIDDEN: {reason}")
        return result

    def can_advance(self, phase: PhaseType) -> bool:
        gate = self.db.get_phase_gate(phase)
        return gate is not None and gate["status"] in (
            GateStatus.PASS.value, GateStatus.OVERRIDE.value)
