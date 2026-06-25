"""Assignment-scoped phase gate tests (FR-034)."""
import pytest

from ado2gh.assignments.store import AssignmentStore
from ado2gh.models import MigrationStatus, PhaseType, RepoConfig
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.state.db import StateDB


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "gate.db")
    return StateDB(path)


def test_assignment_gate_fail_empty_cohort(db):
    checker = PhaseGateChecker(db)
    result = checker.check_for_assignment(PhaseType.PILOT, [])
    assert result.status.value == "fail"


def test_assignment_gate_pass_with_completed(db):
    store = AssignmentStore(db)
    a = store.create(
        profile_id="p1",
        name="pilot",
        assignment_type=__import__(
            "ado2gh.assignments.models", fromlist=["AssignmentType"]
        ).AssignmentType.PILOT,
        execution_phase="pilot",
        repos=[{"ado_project": "P", "ado_repo": "r1", "gh_org": "o", "gh_repo": "r1"}],
    )
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    for scope in ["repo", "work_items", "pipelines", "wiki", "secrets", "branch_policies"]:
        db.upsert_migration(1, repo, scope, MigrationStatus.COMPLETED)
    checker = PhaseGateChecker(db)
    result = checker.check_for_assignment(PhaseType.PILOT, ["r1"])
    assert result.repos_total == 1
    assert result.repos_completed == 1
