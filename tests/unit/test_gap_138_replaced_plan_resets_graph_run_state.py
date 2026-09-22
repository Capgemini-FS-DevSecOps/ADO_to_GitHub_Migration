"""Regression check for register entry GAP-138.

The `generate_plan` tool (`nodes/orchestrator_tools.py`) replaces
`session["migration_plan"]` directly, but the LangGraph state for the turn is
a separate dict — `graph/builder.py`'s `_route_after_orchestrator` reads
`state.get("migration_plan")` and `state.get("start_execution")`, not the
session. Left unpatched, a stale `start_execution=True` plus the *old* plan
still sitting in `state` (carried over from a previous turn's checkpoint, or
from within the same turn's queued run) would route the executor straight to
the old migration queue instead of presenting the freshly-installed plan for
approval.

This test builds a state that mimics exactly that leftover shape, runs the
`generate_plan` tool path, and checks the router never sends a
tool-installed replacement to the executor.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.graph.builder import _route_after_orchestrator
from ado2gh.agents.migration_agent.hitl.intake import record_plan_approval
from ado2gh.agents.migration_agent.nodes.orchestrator_tools import _execute_orchestrator_tools


def _plan(revision: int) -> dict:
    return {
        "revision": revision,
        "dry_run": True,
        "repos": [{"id": "Proj/app"}],
        "work_items": [{"scope": "repo", "repo": "Proj/app", "status": "ready"}],
    }


def _stale_in_flight_state(session: dict, old_plan: dict) -> dict:
    """A graph state shaped like a run that was already underway."""
    return {
        "session": session,
        "start_execution": True,
        "migration_plan": old_plan,
        "migration_queue": {"items": [{"repo": "Proj/app"}], "current_index": 0},
        "executor_result": {"status": "ok"},
        "validation_result": {"passed": True},
        "validation_feedback": {"escalate": False},
        "should_return": False,
        "start_pev": False,
    }


@pytest.mark.asyncio
async def test_router_does_not_send_a_replaced_plan_to_the_executor():
    old_plan = _plan(revision=0)
    session = {"migration_plan": old_plan, "messages": [], "pev_execution_started": True}
    record_plan_approval(session)

    new_plan = _plan(revision=1)

    async def build_plan(session, session_token, *, phase=None, repository_id=None):
        return new_plan

    old_state = _stale_in_flight_state(session, old_plan)

    tool_out = await _execute_orchestrator_tools(
        {**old_state, "build_plan": build_plan, "user_message": ""},
        [{"name": "generate_plan", "arguments": {"repository_id": "Proj/app"}}],
    )
    new_state = {**old_state, **tool_out}

    assert _route_after_orchestrator(new_state) != "executor"


@pytest.mark.asyncio
async def test_replaced_plan_patch_resets_every_run_state_key():
    old_plan = _plan(revision=0)
    session = {"migration_plan": old_plan, "messages": []}
    record_plan_approval(session)

    new_plan = _plan(revision=1)

    async def build_plan(session, session_token, *, phase=None, repository_id=None):
        return new_plan

    old_state = _stale_in_flight_state(session, old_plan)

    tool_out = await _execute_orchestrator_tools(
        {**old_state, "build_plan": build_plan, "user_message": ""},
        [{"name": "generate_plan", "arguments": {"repository_id": "Proj/app"}}],
    )
    new_state = {**old_state, **tool_out}

    assert new_state["migration_plan"] == new_plan
    assert new_state["start_execution"] is False
    assert new_state["migration_queue"] is None
    assert new_state["executor_result"] is None
    assert new_state["validation_result"] is None
    assert new_state["validation_feedback"] is None
