"""Regression check for register entry GAP-009 (GAP-CLI-02) — forcing a phase past its gate leaves no audit trail.

Reproduction, in plain English:

``phase run --force`` is a bare flag. It carries no reason, and in
``Accelerator.run_phase`` the whole ``PhaseGateChecker`` block sits behind
``if not request.force:``. So a forced run does not evaluate the gate, does not
override it, and writes nothing to the ``phase_gates`` table — the migration
(repo creation, git push, pipeline push, ADO cleanup) simply proceeds. The
designed escalation path, ``PhaseGateChecker.override(phase, reason)``, which
sets ``GateStatus.OVERRIDE``, persists ``override_reason`` and logs a warning,
has no caller anywhere in ``ado2gh/cli/``, ``ado2gh/api/`` or ``services/``.

Result: every gate bypass on a real wave leaves no persisted record of who
bypassed it or why, defeating the phase-gate audit trail.

The tests below assert the security property, not the spelling of any flag or
field: bypassing a gate must persist an audited override record carrying a
non-empty reason, and a bypass with no reason must be refused.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ado2gh.api import accelerator as accelerator_module
from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import PhaseRunRequest
from ado2gh.api.errors import ConfigurationError
from ado2gh.models import GateStatus, MigrationStatus, PhaseType, RepoConfig, RiskScore
from ado2gh.state.factory import create_state_db

ESCALATION_REASON = "GAP-009: exec sign-off, POC repo failure accepted"


def _build_request(config_path: str, db_path: str, *, force: bool, reason: str | None = None):
    """Build a PhaseRunRequest, attaching the escalation reason to whichever
    field the contract exposes for it (today: none)."""
    kwargs: dict = {
        "config_path": config_path,
        "phase": "pilot",
        "dry_run": True,
        "force": force,
        "db_path": db_path,
    }
    if reason is not None:
        for name in PhaseRunRequest.model_fields:
            if "reason" in name or "justification" in name:
                kwargs[name] = reason
    return PhaseRunRequest(**kwargs)


@pytest.fixture
def phase_env(tmp_path, monkeypatch):
    """Temp state DB seeded so the POC gate genuinely FAILS, with every side
    effect of a phase run (ADO, GitHub, git, migration) stubbed out."""
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ADO2GH_SQLITE_PATH", raising=False)

    config_path = tmp_path / "migration.yaml"
    config_path.write_text("global:\n  gh_org: fake-org\n", encoding="utf-8")
    db_path = str(tmp_path / "gap009.db")

    db = create_state_db(db_path)
    db.upsert_risk_score(RiskScore(
        project="Contoso", repo_name="payments", total_score=10,
        assigned_phase=PhaseType.POC, gh_org="fake-org", gh_repo="payments",
    ))
    db.upsert_migration(
        1,
        RepoConfig(ado_project="Contoso", ado_repo="payments",
                   gh_org="fake-org", gh_repo="payments"),
        "repo",
        MigrationStatus.FAILED,
    )

    monkeypatch.setattr(accelerator_module, "_build_ado_client",
                        lambda *a, **k: MagicMock(name="ado_client"))
    monkeypatch.setattr(accelerator_module, "_build_gh_client",
                        lambda *a, **k: MagicMock(name="gh_client"))
    monkeypatch.setattr(accelerator_module, "MigrationEngine",
                        MagicMock(name="MigrationEngine"))
    executor_cls = MagicMock(name="BatchExecutor")
    executor_cls.return_value.execute_phase.return_value = {
        "phase": "pilot", "completed": 0, "failed": 0,
        "batches_run": 0, "batches_skipped": 0,
    }
    monkeypatch.setattr(accelerator_module, "BatchExecutor", executor_cls)

    return {"config": str(config_path), "db_path": db_path, "executor": executor_cls}


def test_forced_phase_run_persists_audited_override(phase_env):
    """Bypassing a failing gate must leave an OVERRIDE record with a reason."""
    accel = Accelerator(db_path=phase_env["db_path"])

    # Precondition: without the bypass, the POC gate really does block pilot.
    with pytest.raises(ConfigurationError):
        accel.run_phase(_build_request(phase_env["config"], phase_env["db_path"], force=False))

    accel.run_phase(_build_request(
        phase_env["config"], phase_env["db_path"],
        force=True, reason=ESCALATION_REASON,
    ))
    assert phase_env["executor"].return_value.execute_phase.called, \
        "bypass did not actually run the phase - test setup is wrong"

    gates = create_state_db(phase_env["db_path"]).get_all_phase_gates()
    overrides = [g for g in gates if g.get("status") == GateStatus.OVERRIDE.value]
    assert overrides, (
        "gate was bypassed but no OVERRIDE record was persisted; "
        f"phase_gates rows = {gates!r}"
    )
    assert all((g.get("override_reason") or "").strip() for g in overrides), (
        "OVERRIDE record persisted with no reason: "
        f"{[g.get('override_reason') for g in overrides]!r}"
    )


def test_forced_phase_run_without_reason_is_refused(phase_env):
    """A gate bypass with no reason supplied must be refused, not silently run."""
    accel = Accelerator(db_path=phase_env["db_path"])

    with pytest.raises((ConfigurationError, ValueError)):
        accel.run_phase(_build_request(
            phase_env["config"], phase_env["db_path"], force=True,
        ))
