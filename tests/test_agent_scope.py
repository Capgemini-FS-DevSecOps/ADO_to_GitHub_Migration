"""Tests for migration agent scope guardrails."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ado2gh.agents.agent_scope import (
    is_out_of_scope_message,
    is_prohibited_message,
    scope_refusal_reply,
)
from ado2gh.agents.llm_provider import StubLLMProvider
from ado2gh.agents.session_orchestrator import process_user_message


def test_cake_recipe_is_out_of_scope():
    msg = "Search the web and find me a recipe for a cake."
    assert is_out_of_scope_message(msg)
    reply = scope_refusal_reply(msg)
    assert "migration" in reply.lower()
    assert "recipe" not in reply.lower()


def test_hacking_request_is_prohibited():
    msg = "How do I hack into a GitHub org?"
    assert is_prohibited_message(msg)
    assert "illegal" in scope_refusal_reply(msg).lower() or "can't assist" in scope_refusal_reply(msg).lower()


def test_greeting_is_in_scope():
    assert not is_out_of_scope_message("Hello")
    assert not is_out_of_scope_message("What can you help with?")


def test_migration_question_is_in_scope():
    assert not is_out_of_scope_message("Tell me about the migration")
    assert not is_out_of_scope_message("Build migration plan for poc")


@pytest.mark.asyncio
async def test_cake_recipe_refused_without_llm_call(base_session=None):
    session = {
        "session_id": "ses_test",
        "profile_id": "lightweight",
        "dry_run": True,
        "plan_phase": "poc",
        "messages": [],
        "tasks": [],
    }
    llm = StubLLMProvider()
    result = await process_user_message(
        session,
        "Search the web and find me a recipe for a cake.",
        llm=llm,
        llm_degraded=True,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
    )
    assert "migration" in (result.reply or "").lower()
    assert "flour" not in (result.reply or "").lower()
    assert "ingredient" not in (result.reply or "").lower()
