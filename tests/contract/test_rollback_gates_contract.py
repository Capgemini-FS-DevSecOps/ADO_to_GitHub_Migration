"""Contract smoke tests for rollback-gates API shapes."""


def test_gate_status_shape():
    from ado2gh.api.agentic_routes import _gate_payload
    from ado2gh.models import PhaseType
    from ado2gh.phase.gate_checker import PhaseGateChecker
    from ado2gh.state.db import StateDB
    import tempfile
    db = StateDB(tempfile.mktemp(suffix=".db"))
    checker = PhaseGateChecker(db)
    result = checker.check_for_assignment(PhaseType.POC, [])
    payload = _gate_payload("asgn_test", PhaseType.POC, result)
    assert "can_advance" in payload
    assert "repo_success_pct" in payload
