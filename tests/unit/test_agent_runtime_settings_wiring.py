"""A persisted AgentRuntimeSettings override must actually change behaviour.

test_agent_runtime_settings.py covers the settings dataclass and its accessor
in isolation. This file is the other half: with agent_runtime_settings()
monkeypatched to return changed values, do the two call sites that read it
directly from this task -- the graph builder's iteration ceiling and the LLM
bridge's call timeout -- actually reflect the override, rather than a module
constant frozen at import time.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ado2gh.agents.migration_agent.graph import builder
from ado2gh.agents.migration_agent.runtime import llm_bridge


def _fake_settings(**overrides: object) -> SimpleNamespace:
    """A stand-in AgentRuntimeSettings with only the fields a test needs."""
    return SimpleNamespace(**overrides)


def test_route_after_validator_uses_the_patched_iteration_ceiling(monkeypatch):
    """`_route_after_validator` reads its ceilings from agent_runtime_settings()
    at call time, so a patched max_iterations changes routing without editing
    the constant or the graph state's own max_iterations override.
    """
    monkeypatch.setattr(
        builder,
        "agent_runtime_settings",
        lambda: _fake_settings(max_iterations=5, max_pev_retries=1),
    )
    state = {
        "validation_result": {"passed": False},
        "validation_feedback": {},
        "pev_retry_count": 0,
    }

    # Below the patched ceiling (5): retries to the planner.
    assert builder._route_after_validator({**state, "iteration": 4}) == "planner"
    # At the patched ceiling: stops the loop instead of running away to 20.
    assert builder._route_after_validator({**state, "iteration": 5}) == "orchestrator"


def test_build_langchain_chat_model_uses_the_patched_llm_timeout(monkeypatch):
    """build_langchain_chat_model() passes agent_runtime_settings().llm_timeout_seconds
    straight through to the LangChain client, so a patched value shows up on
    the built model instead of the bridge's old hardcoded 60.
    """
    monkeypatch.setattr(
        llm_bridge,
        "agent_runtime_settings",
        lambda: _fake_settings(llm_timeout_seconds=123),
    )
    cfg = MagicMock(
        enabled=True,
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_id="gpt-4",
    )
    model = llm_bridge.build_langchain_chat_model(cfg, llm_bridge.ModelCapabilities())
    assert model is not None
    assert model.request_timeout == 123
