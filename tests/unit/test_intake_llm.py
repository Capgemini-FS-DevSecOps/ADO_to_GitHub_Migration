"""Tests for LLM-driven operator message analysis."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ado2gh.agents.migration_agent.intake_llm import analyze_operator_message
from ado2gh.agents.migration_agent.intake_schema import OperatorIntent


@pytest.mark.asyncio
async def test_analyze_operator_message_parses_json_response():
    llm = AsyncMock()
    llm.with_structured_output = MagicMock(side_effect=RuntimeError("no structured output"))
    llm.ainvoke.return_value = MagicMock(
        content=json.dumps({
            "reasoning": "Operator wants to migrate another repository in live mode.",
            "intent": "migration_action",
            "dry_run": False,
            "requests_new_migration": True,
        }),
    )

    analysis = await analyze_operator_message(
        llm,
        "migrate another one live",
        {"status": "idle", "last_completed_repository_id": "proj/prev"},
    )

    assert analysis is not None
    assert analysis.intent == OperatorIntent.MIGRATION_ACTION
    assert analysis.dry_run is False
    assert analysis.requests_new_migration is True
    assert analysis.repository_id is None


@pytest.mark.asyncio
async def test_analyze_operator_message_returns_none_on_failure():
    llm = AsyncMock()
    llm.with_structured_output = MagicMock(side_effect=RuntimeError("no structured output"))
    llm.ainvoke.side_effect = RuntimeError("llm down")

    analysis = await analyze_operator_message(llm, "migrate foo", {})
    assert analysis is None
