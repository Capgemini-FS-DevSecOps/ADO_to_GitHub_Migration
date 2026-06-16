"""Executor tool whitelist."""
from ado2gh.agents.executor import AgentExecutor, EXECUTOR_WHITELIST


def test_whitelist_blocks_unknown():
    ex = AgentExecutor()
    assert not ex.can_invoke("ado2gh_delete_everything")


def test_live_rollback_blocked():
    ex = AgentExecutor({"ado2gh_rollback": lambda p: p})
    out = ex.invoke("ado2gh_rollback", {"dry_run": False}, approver_ok=False)
    assert "error" in out


def test_whitelist_contains_core_tools():
    assert "ado2gh_enqueue_job" in EXECUTOR_WHITELIST
    assert "ado2gh_rollback" in EXECUTOR_WHITELIST
