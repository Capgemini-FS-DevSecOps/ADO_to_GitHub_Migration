"""Assignment resolver NL cohort resolution."""
from ado2gh.assignments.models import AssignmentType
from ado2gh.assignments.resolver import AssignmentResolver
from ado2gh.assignments.store import AssignmentStore
from ado2gh.state.db import StateDB


def test_resolve_pilot(tmp_path):
    db = StateDB(str(tmp_path / "res.db"))
    store = AssignmentStore(db)
    store.create(
        profile_id="p1",
        name="Pilot cohort",
        assignment_type=AssignmentType.PILOT,
        execution_phase="pilot",
        repos=[{"ado_project": "P", "ado_repo": "r1"}],
    )
    resolver = AssignmentResolver(store)
    a = resolver.resolve("p1", "run pilot migration")
    assert a is not None
    assert a.assignment_type == AssignmentType.PILOT
