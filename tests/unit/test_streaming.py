"""Unit tests for SSE streaming — event generation, kinds, subagent labels."""
import pytest

from ado2gh.agents.migration_agent.runtime.orchestrator import (
    _build_initial_state,
    _node_to_subagent,
    stream_user_message,
)
from ado2gh.agents.migration_agent.nodes import _stream_llm_response


def test_build_initial_state_defaults():
    session = {"session_id": "ses_test", "messages": []}
    state = _build_initial_state(session, "hello")
    assert state["user_message"] == "hello"
    assert state["session"] is session
    assert state["intent"] == ""
    assert state["iteration"] == 0
    assert state["max_iterations"] == 20
    assert state["should_return"] is False
    assert state["start_pev"] is False
    assert state["tool_calls"] == []
    assert state["pev_retry_count"] == 0


def test_build_initial_state_with_model_id():
    session = {"session_id": "ses_test", "messages": []}
    state = _build_initial_state(session, "hi", model_id="nonexistent")
    # Should not crash; llm now flows via runtime deps, not state — degrade flag must be set
    assert state["llm_unconfigured"] is True


def test_node_to_subagent_mapping():
    assert _node_to_subagent("orchestrator") == "orchestrator"
    assert _node_to_subagent("planner") == "planner"
    assert _node_to_subagent("executor") == "executor"
    assert _node_to_subagent("validator") == "validator"
    assert _node_to_subagent("classify_intent") == "orchestrator"
    assert _node_to_subagent("execute_tools") == "orchestrator"
    assert _node_to_subagent("finalize") == "orchestrator"
    assert _node_to_subagent("unknown") == "orchestrator"


def test_sse_event_kinds():
    """Verify expected SSE event kinds are defined."""
    expected_kinds = {"token", "thinking", "tool_call", "tool_result", "status", "message", "heartbeat", "form_request", "done"}
    # These are the kinds emitted by the streaming orchestrator
    # Verify they appear in the codebase
    from ado2gh.agents.migration_agent.runtime.orchestrator import (
        _stream_graph_events,
        stream_user_message,
    )
    import inspect
    # stream_user_message is now a thin wrapper; actual event kinds are emitted
    # from _stream_graph_events, which it delegates to.
    source = inspect.getsource(stream_user_message) + inspect.getsource(_stream_graph_events)
    for kind in ("token", "thinking", "message", "tool_call", "form_request", "done"):
        assert kind in source, f"Missing SSE event kind '{kind}' in streaming orchestrator"


@pytest.mark.asyncio
async def test_stream_llm_response_fallback():
    """Test that streaming falls back to invoke when astream unavailable."""
    class FakeLLM:
        async def astream(self, messages):
            raise NotImplementedError("No streaming")
        async def ainvoke(self, messages):
            class Result:
                content = "Fallback response"
            return Result()

    state = {}
    result = await _stream_llm_response(FakeLLM(), [], state, subagent="orchestrator")
    assert result == "Fallback response"
    assert len(state["_streaming_tokens"]) == 1
    assert state["_streaming_tokens"][0]["content"] == "Fallback response"


@pytest.mark.asyncio
async def test_stream_llm_response_streaming():
    """Test that streaming yields tokens correctly."""
    class FakeChunk:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            for word in ["Hello", " world", "!"]:
                yield FakeChunk(word)

    state = {}
    result = await _stream_llm_response(FakeLLM(), [], state, subagent="orchestrator")
    assert result == "Hello world!"
    assert len(state["_streaming_tokens"]) == 3
    assert state["_streaming_tokens"][0]["content"] == "Hello"
    assert state["_streaming_tokens"][0]["subagent"] == "orchestrator"
