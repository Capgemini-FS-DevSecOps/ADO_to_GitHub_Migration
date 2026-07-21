"""Test planner does not loop after plan generation."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ado2gh.agents.migration_agent.graph import _route_after_orchestrator, _route_after_planner
from ado2gh.agents.migration_agent.nodes import _apply_orchestrator_tools, _orchestrator_node_impl


def test_route_after_planner_to_orchestrator_when_plan_ready():
    state = {
        "should_return": False,
        "migration_plan": {"repos": [{"id": "azure-pipelines/repo"}]},
        "session": {"plan_approved": False},
        "planner_next": "orchestrator",
    }
    assert _route_after_planner(state) == "orchestrator"


def test_route_after_orchestrator_does_not_reenter_planner_with_plan():
    state = {
        "should_return": True,
        "start_pev": True,
        "pending_form": {"form_id": "intake_plan_review"},
        "migration_plan": {"repos": [{"id": "azure-pipelines/repo"}]},
        "session": {"plan_approved": False, "migration_plan": {"repos": [{}]}},
    }
    assert _route_after_orchestrator(state) == "finalize"



@pytest.mark.asyncio
async def test_dry_run_reply_invokes_planner_when_repo_selected():
    """Operator saying 'dry run' after repo selection must start PEV, not idle out."""
    session = {
        "plan_repository_id": "azure-pipelines/bicep-template-migration",
        "dry_run": True,
        "execution_mode_confirmed": True,
        "status": "idle",
        "messages": [],
    }
    state = {
        "user_message": "dry run",
        "session": session,
        "intent": "general_chat",
        "llm": None,
        "llm_unconfigured": True,
        "accel_get": AsyncMock(return_value={"repos": []}),
        "session_token": None,
    }

    with patch(
        "ado2gh.agents.migration_agent.nodes._classify_user_intent",
        new_callable=AsyncMock,
        return_value={"intent": "general_chat"},
    ):
        result = await _orchestrator_node_impl(state)

    assert result.get("tool_calls")
    assert result["tool_calls"][0]["name"] == "invoke_planner"

    merged = await _apply_orchestrator_tools({**state, **result}, result)
    assert merged.get("start_pev") is True
    assert merged.get("migration_plan") is None
