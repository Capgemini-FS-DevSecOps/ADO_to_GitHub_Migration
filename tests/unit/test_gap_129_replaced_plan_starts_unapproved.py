"""Regression check for register entry GAP-129.

`clear_stale_plan_approval` returned early whenever a run was in flight
(`pev_execution_started`/`pev_execution_completed`), so an approval survived —
by design, for the normal replan that happens mid-run. But the orchestrator's
`generate_plan` tool path (`nodes/orchestrator_tools.py`) stores a brand-new
plan straight onto the session, bypassing the planner-approval flow that would
otherwise have re-checked the approval. A plan swapped in that way inherited
the approval of the plan the operator actually saw, and — because the early
return also covered the in-flight case — it kept running unattended even
mid-run. A plan replaced by a tool is a different plan and must start
unapproved regardless of the execution flags.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.hitl.intake import (
    PLAN_APPROVAL_KEY,
    clear_stale_plan_approval,
    record_plan_approval,
)


def _plan(revision: int = 0, repo: str = "Proj/app") -> dict:
    return {
        "revision": revision,
        "dry_run": True,
        "repos": [{"id": repo}],
        "work_items": [{"scope": "repo", "repo": repo, "status": "ready"}],
    }


def test_replaced_plan_clears_approval_even_mid_run():
    session = {"migration_plan": _plan(), "pev_execution_started": True}
    record_plan_approval(session)

    new_plan = _plan(revision=1)

    assert clear_stale_plan_approval(session, plan=new_plan, replaced_plan=True) is True
    assert session["plan_approved"] is False
    assert PLAN_APPROVAL_KEY not in session
    assert session["pev_execution_started"] is False


def test_replaced_plan_also_clears_execution_completed():
    session = {"migration_plan": _plan(), "pev_execution_completed": True}
    record_plan_approval(session)

    new_plan = _plan(revision=2)

    assert clear_stale_plan_approval(session, plan=new_plan, replaced_plan=True) is True
    assert session["pev_execution_completed"] is False


def test_replaced_plan_that_matches_the_approved_revision_is_left_alone():
    """No swap actually happened — the same plan is not a stale approval."""
    session = {"migration_plan": _plan(), "pev_execution_started": True}
    record_plan_approval(session)

    same_plan = session["migration_plan"]

    assert clear_stale_plan_approval(session, plan=same_plan, replaced_plan=True) is False
    assert session["plan_approved"] is True
    assert session["pev_execution_started"] is True


@pytest.mark.parametrize("flag", ["pev_execution_started", "pev_execution_completed"])
def test_a_normal_replan_still_keeps_its_mid_run_exemption(flag):
    """`replaced_plan` is opt-in: the ordinary replan path is unaffected (THR-09-002)."""
    session = {"migration_plan": _plan(), flag: True}
    record_plan_approval(session)

    session["migration_plan"] = _plan(revision=1)

    assert clear_stale_plan_approval(session) is False
    assert session["plan_approved"] is True


@pytest.mark.asyncio
async def test_orchestrator_generate_plan_tool_starts_the_new_plan_unapproved():
    """End-to-end through the actual bypass site named in GAP-129."""
    from ado2gh.agents.migration_agent.nodes.orchestrator_tools import _execute_orchestrator_tools

    old_plan = _plan()
    session = {"migration_plan": old_plan, "messages": [], "pev_execution_started": True}
    record_plan_approval(session)
    assert session["plan_approved"] is True

    new_plan = _plan(revision=1)

    async def build_plan(session, session_token, *, phase=None, repository_id=None):
        return new_plan

    await _execute_orchestrator_tools(
        {"session": session, "build_plan": build_plan, "user_message": ""},
        [{"name": "generate_plan", "arguments": {"repository_id": "Proj/app"}}],
    )

    assert session["migration_plan"] == new_plan
    assert session["plan_approved"] is False
    assert PLAN_APPROVAL_KEY not in session
    assert session["pev_execution_started"] is False
