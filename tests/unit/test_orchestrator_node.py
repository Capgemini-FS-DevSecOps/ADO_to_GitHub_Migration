"""Orchestrator node routing and LLM intent classification."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ado2gh.agents.migration_agent.hitl.schemas import OperatorIntent, OperatorMessageAnalysis
from ado2gh.agents.migration_agent.nodes import _classify_user_intent


@pytest.mark.asyncio
async def test_classify_user_intent_uses_llm_analysis():
    session = {"status": "idle", "messages": []}
    state = {
        "user_message": "migrate another one live",
        "session": session,
        "llm": object(),
        "llm_unconfigured": False,
    }
    analysis = OperatorMessageAnalysis(
        reasoning="Wants a new live migration without naming the repo.",
        intent=OperatorIntent.MIGRATION_ACTION,
        dry_run=False,
        requests_new_migration=True,
    )

    with patch(
        "ado2gh.agents.migration_agent.hitl.intake_llm.analyze_operator_message",
        new_callable=AsyncMock,
        return_value=analysis,
    ):
        result = await _classify_user_intent(state)

    assert result["intent"] == "migration_action"
    assert result["operator_message_analysis"]["requests_new_migration"] is True
    assert session["dry_run"] is False
    assert session["execution_mode_confirmed"] is True


@pytest.mark.asyncio
async def test_classify_user_intent_requires_llm():
    session = {"status": "idle", "messages": []}
    state = {
        "user_message": "migrate proj/repo",
        "session": session,
        "llm": None,
        "llm_unconfigured": True,
    }

    result = await _classify_user_intent(state)

    assert result["should_return"] is True
    assert result["intent"] == "general_chat"
    assert result.get("reply")
