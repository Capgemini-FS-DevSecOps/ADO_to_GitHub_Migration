"""Regression check for register entry GAP-017 — ``ado2gh phase gate-check`` could not be invoked at all.

Reproduction, in plain English:

``ado2gh/cli/phase.py`` called ``checker.check(PhaseType(phase_name),
override=override, reason=reason)``. ``PhaseGateChecker.check`` takes only the
phase, so every invocation of step 8 of the documented execution workflow died
with ``TypeError`` before reading a single row. No ``phase_gates`` row was ever
written through the CLI, which left ``can_advance()`` permanently False for
every phase and ``phase run --force`` as the only way forward — the unaudited
bypass recorded as GAP-009.

The signature was repaired incidentally by ``ceb6b0b`` (GAP-009), which split
the CLI into ``checker.override(phase, reason)`` and ``checker.check(phase)``.
These tests are the guard that keeps it repaired. They assert the behaviour the
command's own help promises, not the spelling of any internal call: a clean
exit and a persisted gate row for the plain check, an ``override`` row carrying
the operator's reason for the escalation, and a refusal when ``--override``
arrives without one (CA-002).
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from ado2gh.cli.main import cli
from ado2gh.models import (
    GateStatus,
    MigrationStatus,
    PhaseType,
    RepoConfig,
    RiskScore,
)
from ado2gh.state.factory import create_state_db

GH_ORG = "fake-org"
PROJECT = "Contoso"
REPO = "payments"


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    """A phase config plus a state DB holding one fully migrated poc repo.

    The repo is scored into ``poc`` and every one of its migration scopes is
    ``completed``, so the gate has something real to measure and passes.
    """
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = str(tmp_path / "gate.db")
    # ``create_state_db`` reads ADO2GH_SQLITE_PATH ahead of the --db value, and
    # the suite conftest points it at a different temp file for every test.
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)

    config_path = tmp_path / "migration_phase.yaml"
    config_path.write_text(f"global:\n  gh_org: {GH_ORG}\n", encoding="utf-8")

    db = create_state_db(db_path)
    db.upsert_risk_score(RiskScore(
        project=PROJECT, repo_name=REPO, total_score=4.0,
        assigned_phase=PhaseType.POC.value, gh_org=GH_ORG, gh_repo=REPO,
    ))
    db.upsert_migration(
        wave_id=1,
        repo=RepoConfig(ado_project=PROJECT, ado_repo=REPO,
                        gh_org=GH_ORG, gh_repo=REPO),
        scope="repo",
        status=MigrationStatus.COMPLETED,
    )
    return config_path, db_path, db


def _gate_check(config_path, db_path, *extra):
    """Invoke ``phase gate-check`` for the poc phase through Click."""
    return CliRunner().invoke(cli, [
        "phase", "gate-check", "-c", str(config_path), "-p", "poc",
        "--db", db_path, *extra,
    ])


def test_gate_check_runs_and_persists_the_gate(gate_env):
    """The plain check exits 0 and writes the phase's gate row."""
    config_path, db_path, db = gate_env

    result = _gate_check(config_path, db_path)

    assert result.exit_code == 0, (
        f"phase gate-check exited {result.exit_code}: "
        f"{result.exception!r}\n{result.output}"
    )
    row = db.get_phase_gate(PhaseType.POC)
    assert row is not None, "phase gate-check wrote no phase_gates row"
    assert row["status"] == GateStatus.PASS.value
    assert row["repos_completed"] == 1
    assert row["repos_total"] == 1


def test_gate_check_override_persists_the_reason(gate_env):
    """``--override --reason`` exits 0 and records an OVERRIDE row with the reason."""
    config_path, db_path, db = gate_env
    reason = "Accepted by release board on 2026-09-12"

    result = _gate_check(config_path, db_path, "--override", "--reason", reason)

    assert result.exit_code == 0, (
        f"phase gate-check --override exited {result.exit_code}: "
        f"{result.exception!r}\n{result.output}"
    )
    row = db.get_phase_gate(PhaseType.POC)
    assert row is not None, "phase gate-check --override wrote no phase_gates row"
    assert row["status"] == GateStatus.OVERRIDE.value
    assert row["override_reason"] == reason, "the audit trail lost the reason"


def test_gate_check_override_without_reason_is_refused(gate_env):
    """An override with no justification is rejected and writes nothing (CA-002)."""
    config_path, db_path, db = gate_env

    result = _gate_check(config_path, db_path, "--override", "--reason", "   ")

    assert result.exit_code != 0, "an unjustified override must not be accepted"
    assert "--reason" in result.output
    assert db.get_phase_gate(PhaseType.POC) is None, (
        "a refused override still wrote a gate row"
    )
