"""Regression check for register entry GAP-052 — ``ado2gh phase assign`` cannot run at all.

Reproduction, in plain English:

``ado2gh/cli/phase.py`` called ``RiskScorer(ado, state).score_all()`` and
``WaveAssigner(state).assign_and_write(scores, config)``. Neither class takes
those constructor arguments and neither method exists, so every invocation of
step 5 of the documented execution workflow died with ``TypeError`` /
``AttributeError`` before touching ADO. Nothing was scored, no
``repo_risk_scores`` row was written, and no ``migration_phase.yaml`` was
produced — which leaves ``phase run`` with no repos assigned to any phase and
``phase gate-check`` with nothing to measure.

The tests below assert the behaviour the command's own help and CLAUDE.md
promise, not the spelling of any internal call: a successful exit, a phase
config on disk holding the scored repos, and risk scores persisted under the
phase they were assigned to.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from ado2gh.cli import phase as phase_cli
from ado2gh.cli.main import cli
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.models import PhaseType
from ado2gh.state.factory import create_state_db

GH_ORG = "fake-org"


class _StubADO:
    """Minimal ADO client: one project, one live repo and one disabled repo."""

    def list_projects(self) -> list[dict]:
        return [{"id": "proj-1", "name": "Contoso"}]

    def list_repos(self, project: str) -> list[dict]:
        assert project == "Contoso"
        return [
            {"id": "repo-1", "name": "payments", "size": 4096},
            {"id": "repo-2", "name": "retired", "size": 10, "isDisabled": True},
        ]

    def list_variable_groups(self, project: str) -> list[dict]:
        return [{"name": "shared-vars"}]

    def list_service_connections(self, project: str) -> list[dict]:
        return []

    def get_repo_stats(self, project: str, repo_id: str) -> dict:
        return {"branch_count": 7}

    def get_repo_commits(self, project: str, repo_id: str, top: int = 1) -> list[dict]:
        return [{"committer": {"date": "2026-08-01T12:00:00Z"}}]


@pytest.fixture
def assign_env(tmp_path, monkeypatch):
    """Temp config plus state DB, with client construction stubbed out.

    ``load_clients`` is replaced so the command makes no network call and reads
    no developer credential; everything else runs for real.
    """
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    config_path = tmp_path / "migration.yaml"
    config_path.write_text(f"global:\n  gh_org: {GH_ORG}\n", encoding="utf-8")
    db_path = str(tmp_path / "assign.db")
    monkeypatch.setattr(phase_cli, "load_clients", lambda cfg: (_StubADO(), None))
    return config_path, db_path


def _run_assign(config_path, db_path):
    """Invoke ``phase assign`` through Click and require a clean exit."""
    result = CliRunner().invoke(
        cli, ["phase", "assign", "-c", str(config_path), "--db", db_path],
    )
    assert result.exit_code == 0, (
        f"phase assign exited {result.exit_code}: "
        f"{result.exception!r}\n{result.output}"
    )
    return result


def test_phase_assign_writes_a_scored_phase_config(assign_env):
    """The command produces a migration_phase.yaml holding the scored repos."""
    config_path, db_path = assign_env
    _run_assign(config_path, db_path)

    phase_config = config_path.parent / "migration_phase.yaml"
    assert phase_config.exists(), "phase assign wrote no migration_phase.yaml"

    global_cfg, waves = ConfigLoader.load(str(phase_config))
    assert global_cfg.get("gh_org") == GH_ORG
    repos = [r for w in waves for r in w.repos]
    assert [r.ado_repo for r in repos] == ["payments"], "disabled repos must be skipped"
    assert repos[0].ado_project == "Contoso"
    assert repos[0].gh_org == GH_ORG
    assert repos[0].risk_score > 0, "repo was written with no risk score"
    assert repos[0].phase, "repo was written with no phase"
    assert all(w.phase for w in waves), "every wave must name its phase"


def test_phase_assign_persists_risk_scores_for_phase_run(assign_env):
    """The scores land in the state DB, where phase run and gate-check read them."""
    config_path, db_path = assign_env
    _run_assign(config_path, db_path)

    db = create_state_db(db_path)
    rows = db.get_all_risk_scores()
    assert [r["repo_name"] for r in rows] == ["payments"]
    assert rows[0]["total_score"] > 0
    assigned_phase = rows[0]["assigned_phase"]
    assert assigned_phase, "risk score persisted with no phase assignment"
    for_phase = db.get_risk_scores_for_phase(PhaseType(assigned_phase))
    assert [r["repo_name"] for r in for_phase] == ["payments"]
