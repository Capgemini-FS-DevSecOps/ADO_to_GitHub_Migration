"""Contract smoke for dependency logging."""
from ado2gh.api.workflow_readiness import check_workflow_readiness


def test_log_lines_contract():
    r = check_workflow_readiness("P/r1", ["SECRET"], [])
    assert isinstance(r["log_lines"], list)
    assert len(r["log_lines"]) >= 1
