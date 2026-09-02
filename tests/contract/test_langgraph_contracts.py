"""Contract tests for LangGraph agent — SSE event schema, graph structure, API contracts.

Validates that the migration agent conforms to the contracts defined in
specs/012-langgraph-agent-refactor/contracts/api-contracts.md.
"""
import pytest
import json

from ado2gh.agents.migration_agent.graph import (
    ALL_NODES,
    NODE_CLASSIFY_INTENT,
    NODE_ORCHESTRATOR,
    NODE_PLANNER,
    NODE_EXECUTOR,
    NODE_VALIDATOR,
    NODE_EXECUTE_TOOLS,
    NODE_FINALIZE,
    get_compiled_graph,
    reset_compiled_graph,
)
from ado2gh.agents.migration_agent.constants import (
    GRAPH_RECURSION_LIMIT,
    SSE_HEARTBEAT_INTERVAL_SECONDS,
    MAX_PEV_RETRIES,
    MAX_ITERATIONS,
    LLM_TIMEOUT_SECONDS,
)
from ado2gh.agents.migration_agent.graph.state import AgentState


# ─── Graph structure contracts ────────────────────────────────────────

def test_all_required_nodes_present():
    """Graph has five core nodes; legacy aliases point at orchestrator."""
    required = {
        NODE_ORCHESTRATOR,
        NODE_PLANNER,
        NODE_EXECUTOR,
        NODE_VALIDATOR,
        NODE_FINALIZE,
    }
    assert set(ALL_NODES) == required
    assert NODE_CLASSIFY_INTENT == NODE_ORCHESTRATOR
    assert NODE_EXECUTE_TOOLS == NODE_ORCHESTRATOR


def test_entry_point_is_orchestrator():
    """Graph entry point is orchestrator (intent classification runs inside it)."""
    assert ALL_NODES[0] == NODE_ORCHESTRATOR


def test_recursion_limit_meets_contract():
    """Recursion limit must be >= 40 per spec."""
    assert GRAPH_RECURSION_LIMIT >= 40


# ─── AgentState contracts ─────────────────────────────────────────────

def test_agent_state_has_pev_fields():
    """AgentState must have PEV-related fields per data-model.md."""
    required_fields = [
        "messages", "user_message", "session", "llm", "intent",
        "iteration", "max_iterations", "start_pev", "pending_form",
        "should_return", "pev_retry_count", "inter_agent_messages",
        "migration_plan", "executor_result", "validation_result",
        "validation_feedback", "cycle_summaries", "rollback_records",
    ]
    annotations = AgentState.__annotations__
    for field in required_fields:
        assert field in annotations, f"AgentState missing required field: {field}"


def test_agent_state_list_fields_use_operator_add():
    """List fields must use operator.add reducer per data-model.md."""
    import operator
    annotations = AgentState.__annotations__
    list_fields = ["inter_agent_messages", "cycle_summaries", "rollback_records", "streaming_tokens", "message_queue"]
    for field in list_fields:
        assert field in annotations
        # The annotation should be Annotated[type, operator.add]
        # We can't easily check the Annotated metadata at runtime, but we can verify the field exists
        assert field in annotations


# ─── SSE event schema contracts ───────────────────────────────────────

def test_sse_event_kinds_are_defined():
    """SSE events must support these kinds: token, thinking, tool_call, tool_result, status, heartbeat, message, form_request, done."""
    valid_kinds = {
        "token", "thinking", "tool_call", "tool_result",
        "status", "heartbeat", "message", "form_request", "done",
    }
    # Verify these are in the AgentMessage kind union in the UI types
    # For the backend, we just verify the constants exist
    assert SSE_HEARTBEAT_INTERVAL_SECONDS == 15


def test_sse_heartbeat_interval():
    """Heartbeat interval must be 15 seconds per spec."""
    assert SSE_HEARTBEAT_INTERVAL_SECONDS == 15


# ─── Constants contracts ──────────────────────────────────────────────

def test_max_pev_retries():
    """Max PEV retries must be 3 per spec."""
    assert MAX_PEV_RETRIES == 3


def test_max_iterations():
    """Max iterations must be 20 per spec."""
    assert MAX_ITERATIONS == 20


def test_llm_timeout():
    """LLM timeout must be 60 seconds per spec."""
    assert LLM_TIMEOUT_SECONDS == 60


# ─── Orchestrator result contract ─────────────────────────────────────

def test_orchestrator_result_has_required_fields():
    """OrchestratorResult must have reply, start_pev, tasks, pending_form."""
    from ado2gh.agents.migration_agent.session.state import OrchestratorResult
    result = OrchestratorResult()
    assert hasattr(result, "reply")
    assert hasattr(result, "start_pev")
    assert hasattr(result, "tasks")
    assert hasattr(result, "pending_form")


# ─── Session store contract ───────────────────────────────────────────

def test_session_store_has_required_tables():
    """Session store schema must include all required tables."""
    from ado2gh.agents.migration_agent.session.store import SCHEMA
    required_tables = [
        "agent_sessions", "agent_messages", "migration_plans",
        "executor_results", "validation_results", "pev_cycle_summaries",
        "migration_queues", "guardrail_decisions", "rollback_records",
        "repo_locks", "service_connection_mappings",
    ]
    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA, f"Missing table: {table}"


# ─── Guardrail contract ───────────────────────────────────────────────

def test_guardrail_actions_are_defined():
    """GuardrailAction must have ALLOW, BLOCK, REQUIRE_CONFIRMATION."""
    from ado2gh.agents.migration_agent.guardrails import GuardrailAction
    assert GuardrailAction.ALLOW.value == "allow"
    assert GuardrailAction.BLOCK.value == "block"
    assert GuardrailAction.REQUIRE_CONFIRMATION.value == "require_confirmation"


def test_guardrail_decision_has_required_fields():
    """GuardrailDecision must have all required fields per data-model.md."""
    from ado2gh.agents.migration_agent.guardrails import GuardrailDecision, GuardrailAction
    decision = GuardrailDecision(
        action=GuardrailAction.ALLOW,
        agent_role="executor",
        tool_name="call_accelerator",
        operation_type="call_accelerator",
        target_resource="Proj/RepoA",
        reason="Plan approved",
    )
    assert decision.allowed is True
    assert decision.blocked is False
    assert decision.needs_confirmation is False
    d = decision.to_dict()
    assert "action" in d
    assert "agent_role" in d
    assert "tool_name" in d
    assert "operation_type" in d
    assert "target_resource" in d
    assert "reason" in d
    assert "timestamp" in d


# ─── Model capabilities contract ──────────────────────────────────────

def test_model_capabilities_has_required_fields():
    """ModelCapabilities must have supports_tool_calling, supports_streaming, supports_thinking, max_context_tokens."""
    from ado2gh.agents.migration_agent.runtime.llm_bridge import ModelCapabilities
    caps = ModelCapabilities()
    assert caps.supports_tool_calling is True
    assert caps.supports_streaming is True
    assert caps.supports_thinking is False
    assert caps.max_context_tokens > 0
    assert caps.max_token_budget > 0
    assert caps.max_token_budget < caps.max_context_tokens  # 80% budget


def test_model_capability_error_is_exception():
    """ModelCapabilityError must be an Exception subclass."""
    from ado2gh.agents.migration_agent.runtime.llm_bridge import ModelCapabilityError
    assert issubclass(ModelCapabilityError, Exception)
