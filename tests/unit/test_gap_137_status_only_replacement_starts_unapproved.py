"""Regression check for register entry GAP-137.

`clear_stale_plan_approval(replaced_plan=True)` used to compare the incoming
plan's revision key against the approved one and skip clearing when the keys
matched. `plan_revision_key` (`hitl/blockers.py`) deliberately leaves a work
item's `status` out of that key, on purpose, so running a plan never
invalidates the approval that authorised the run.

But a tool that swaps in a brand-new plan outright (the `generate_plan`
bypass named in GAP-129) is not "running the plan" — it is a fresh plan the
operator has not seen. A replacement whose only difference is a work item's
status (a previously skipped item flipped back to ready, for example) keeps
the same key and, under the old code, inherited the earlier approval while
enabling a new write. With `replaced_plan=True` the key comparison is skipped
entirely: any tool-installed replacement clears the approval, unconditionally.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.hitl.blockers import plan_revision_key
from ado2gh.agents.migration_agent.hitl.intake import (
    PLAN_APPROVAL_KEY,
    clear_stale_plan_approval,
    record_plan_approval,
)


def _plan(status: str) -> dict:
    return {
        "revision": 0,
        "dry_run": True,
        "repos": [{"id": "Proj/app"}],
        "work_items": [{"scope": "repo", "repo": "Proj/app", "status": status}],
    }


def test_status_only_change_does_not_change_the_revision_key():
    """Confirms the premise of this gap: the key is designed to ignore status."""
    skipped = _plan("skipped")
    ready = _plan("ready")
    assert plan_revision_key(skipped) == plan_revision_key(ready)


def test_status_only_replacement_still_starts_unapproved():
    session = {"migration_plan": _plan("skipped"), "pev_execution_completed": True}
    record_plan_approval(session)
    assert session["plan_approved"] is True

    replacement = _plan("ready")

    assert clear_stale_plan_approval(session, plan=replacement, replaced_plan=True) is True
    assert session["plan_approved"] is False
    assert PLAN_APPROVAL_KEY not in session
    assert session["pev_execution_completed"] is False


@pytest.mark.asyncio
async def test_orchestrator_generate_plan_tool_clears_a_status_only_replacement():
    """End-to-end through the actual bypass site named in GAP-129/GAP-137."""
    from ado2gh.agents.migration_agent.nodes.orchestrator_tools import _execute_orchestrator_tools

    old_plan = _plan("skipped")
    session = {"migration_plan": old_plan, "messages": [], "pev_execution_started": True}
    record_plan_approval(session)
    assert session["plan_approved"] is True

    new_plan = _plan("ready")

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
