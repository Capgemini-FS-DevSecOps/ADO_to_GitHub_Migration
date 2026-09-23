"""Gate checker override and fail paths."""
from ado2gh.models import MigrationStatus, PhaseType, RepoConfig, RiskScore
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.state.db import StateDB


def test_override_gate(tmp_path):
    db = StateDB(str(tmp_path / "go.db"))
    checker = PhaseGateChecker(db)
    result = checker.override(PhaseType.POC, "executive sign-off")
    assert result.status.value == "override"
    assert checker.can_advance(PhaseType.POC)


def test_check_no_repos(tmp_path):
    db = StateDB(str(tmp_path / "gn.db"))
    checker = PhaseGateChecker(db)
    result = checker.check(PhaseType.PILOT)
    assert result.status.value == "fail"


def test_check_with_risk_scores(tmp_path):
    db = StateDB(str(tmp_path / "gr.db"))
    db.upsert_risk_score(RiskScore(
        project="P", repo_name="r1", total_score=10,
        assigned_phase=PhaseType.POC, gh_org="o", gh_repo="r1",
    ))
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    for scope in ["repo", "work_items", "pipelines", "wiki", "secrets", "branch_policies"]:
        db.upsert_migration(1, repo, scope, MigrationStatus.COMPLETED)
    checker = PhaseGateChecker(db)
    result = checker.check(PhaseType.POC)
    assert result.repos_total >= 1
