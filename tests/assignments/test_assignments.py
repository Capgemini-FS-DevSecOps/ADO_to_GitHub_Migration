"""Assignment CRUD and cohort membership."""
from ado2gh.assignments.models import AssignmentType
from ado2gh.assignments.store import AssignmentStore
from ado2gh.state.db import StateDB


def test_create_and_list(tmp_path):
    db = StateDB(str(tmp_path / "a.db"))
    store = AssignmentStore(db)
    a = store.create(
        profile_id="prof1",
        name="POC cohort",
        assignment_type=AssignmentType.POC,
        execution_phase="poc",
        repos=[{"ado_project": "P", "ado_repo": "r1"}],
    )
    listed = store.list_for_profile("prof1")
    assert len(listed) == 1
    assert listed[0].id == a.id
    assert store.get_execution_phase(a.id) == "poc"


def test_single_active_membership(tmp_path):
    db = StateDB(str(tmp_path / "b.db"))
    store = AssignmentStore(db)
    store.create(
        profile_id="prof1",
        name="A",
        assignment_type=AssignmentType.POC,
        execution_phase="poc",
        repos=[{"ado_project": "P", "ado_repo": "r1"}],
    )
    store.create(
        profile_id="prof1",
        name="B",
        assignment_type=AssignmentType.PILOT,
        execution_phase="pilot",
        repos=[{"ado_project": "P", "ado_repo": "r1"}],
    )
    repos = db.get_cohort_repos(store.list_for_profile("prof1")[1].id)
    assert len(repos) == 1
