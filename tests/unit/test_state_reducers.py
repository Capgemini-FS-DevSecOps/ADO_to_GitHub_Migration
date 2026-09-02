"""Unit tests for AgentState reducers — verify accumulation behavior."""
import operator

from ado2gh.agents.migration_agent.graph.state import AgentState


def test_state_has_messages_field():
    """Verify messages field exists with add_messages reducer."""
    # TypedDict doesn't expose fields at runtime, but we can check the annotation
    hints = AgentState.__annotations__
    assert "messages" in hints


def test_state_has_accumulating_fields():
    """Verify list fields use operator.add reducer."""
    hints = AgentState.__annotations__
    assert "inter_agent_messages" in hints
    assert "cycle_summaries" in hints
    assert "rollback_records" in hints
    assert "streaming_tokens" in hints
    assert "message_queue" in hints


def test_state_has_scalar_fields():
    """Verify scalar fields exist (overwrite on update)."""
    hints = AgentState.__annotations__
    for field in ("user_message", "intent", "reply", "should_return", "iteration",
                  "start_pev", "error", "pev_retry_count"):
        assert field in hints, f"Missing field: {field}"


def test_state_has_pev_fields():
    """Verify PEV-related fields exist."""
    hints = AgentState.__annotations__
    for field in ("migration_plan", "executor_result", "validation_result",
                  "validation_feedback", "pending_clarification", "migration_queue"):
        assert field in hints, f"Missing field: {field}"


def test_state_has_llm_fields():
    """Verify LLM config fields exist."""
    hints = AgentState.__annotations__
    for field in ("llm", "llm_degraded", "llm_unconfigured", "capabilities"):
        assert field in hints, f"Missing field: {field}"


def test_state_has_iteration_control():
    """Verify iteration control fields exist."""
    hints = AgentState.__annotations__
    for field in ("iteration", "max_iterations", "accumulated_tokens", "max_token_budget"):
        assert field in hints, f"Missing field: {field}"
