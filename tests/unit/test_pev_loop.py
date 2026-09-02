"""Unit tests for PEV loop — conditional edges, retry logic, cycle summaries, inter-agent messaging."""
import pytest

from ado2gh.agents.migration_agent.graph import (
    _route_after_orchestrator,
    _route_after_planner,
    _route_after_executor,
    _route_after_validator,
)
from ado2gh.agents.migration_agent.nodes import (
    _make_inter_agent_message,
    _make_cycle_summary,
)


# ─── Conditional edge routing ─────────────────────────────────────────

def test_route_orchestrator_to_planner_when_pev():
    state = {"should_return": False, "start_pev": True}
    assert _route_after_orchestrator(state) == "planner"


def test_route_orchestrator_to_planner_when_execution_requested():
    state = {"should_return": False, "start_execution": True}
    assert _route_after_orchestrator(state) == "planner"


def test_route_planner_to_executor_when_plan_ready():
    state = {
        "migration_plan": {"repos": []},
        "pending_clarification": None,
        "should_return": False,
        "session": {"plan_approved": True},
    }
    assert _route_after_planner(state) == "executor"


def test_route_planner_to_orchestrator_when_plan_needs_confirmation():
    state = {
        "migration_plan": {"repos": []},
        "pending_clarification": None,
        "should_return": False,
        "session": {"plan_approved": False},
    }
    assert _route_after_planner(state) == "orchestrator"


def test_route_planner_to_orchestrator_when_clarification():
    state = {"pending_clarification": {"to_role": "orchestrator"}, "should_return": False}
    assert _route_after_planner(state) == "orchestrator"


def test_route_executor_to_validator_when_done():
    state = {"executor_result": {"per_repo_results": []}, "should_return": False}
    assert _route_after_executor(state) == "validator"


def test_route_executor_finalize_without_result():
    state = {"should_return": False}
    assert _route_after_executor(state) == "finalize"


def test_route_validator_to_planner_on_retry():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 1,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_validator_to_orchestrator_on_pass():
    state = {
        "validation_result": {"passed": True},
        "validation_feedback": None,
        "pev_retry_count": 0,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "orchestrator"


def test_route_validator_to_orchestrator_on_max_retries():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 3,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "orchestrator"


def test_route_validator_to_orchestrator_on_max_iterations():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 1,
        "iteration": 20,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "orchestrator"


# ─── Inter-agent messaging ────────────────────────────────────────────

def test_inter_agent_message_structure():
    msg = _make_inter_agent_message(
        from_role="validator",
        to_role="planner",
        message_type="validation_result",
        payload={"passed": False, "failures": ["git_parity"]},
    )
    assert msg["from_role"] == "validator"
    assert msg["to_role"] == "planner"
    assert msg["message_type"] == "validation_result"
    assert msg["payload"]["passed"] is False
    assert "timestamp" in msg
    assert "message_id" in msg


def test_inter_agent_message_with_correlation_ids():
    msg = _make_inter_agent_message(
        from_role="planner",
        to_role="executor",
        message_type="plan_ready",
        payload={"plan_id": "p1"},
        correlation_ids={"session_id": "ses1", "plan_id": "p1"},
    )
    assert msg["correlation_ids"]["session_id"] == "ses1"
    assert msg["correlation_ids"]["plan_id"] == "p1"


# ─── PevCycleSummary ──────────────────────────────────────────────────

def test_cycle_summary_pass():
    executor_result = {"per_repo_results": [{"repo": "A"}, {"repo": "B"}]}
    validation_result = {"failures": []}
    summary = _make_cycle_summary(1, executor_result, validation_result, "complete")
    assert summary["cycle_number"] == 1
    assert summary["repos_processed"] == 2
    assert summary["repos_succeeded"] == 2
    assert summary["repos_failed"] == 0
    assert summary["next_action"] == "complete"


def test_cycle_summary_fail():
    executor_result = {"per_repo_results": [{"repo": "A"}, {"repo": "B"}]}
    validation_result = {"failures": [{"repo": "A", "scope": "git"}]}
    summary = _make_cycle_summary(2, executor_result, validation_result, "retry_planner")
    assert summary["cycle_number"] == 2
    assert summary["repos_processed"] == 2
    assert summary["repos_succeeded"] == 1
    assert summary["repos_failed"] == 1
    assert summary["next_action"] == "retry_planner"


def test_cycle_summary_failures_capped():
    executor_result = {"per_repo_results": []}
    failures = [{"repo": f"repo_{i}"} for i in range(20)]
    validation_result = {"failures": failures}
    summary = _make_cycle_summary(1, executor_result, validation_result, "retry_planner")
    assert len(summary["failures"]) <= 10  # Capped


# ─── Context window management ────────────────────────────────────────

def test_trim_context_basic():
    from ado2gh.agents.migration_agent.runtime.context_window import build_context_with_cycle_summaries
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content="system")] + [HumanMessage(content=f"msg {i}") for i in range(20)]
    trimmed = build_context_with_cycle_summaries(messages, [], max_tokens=100)
    assert len(trimmed) <= 21
    assert isinstance(trimmed[0], SystemMessage)


def test_trim_context_empty():
    from ado2gh.agents.migration_agent.runtime.context_window import build_context_with_cycle_summaries
    assert build_context_with_cycle_summaries([], [], 1000) == []


def test_build_context_with_cycle_summaries():
    from ado2gh.agents.migration_agent.runtime.context_window import build_context_with_cycle_summaries
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content="system"), HumanMessage(content="hello")]
    summaries = [{"cycle_number": i, "next_action": "retry"} for i in range(5)]
    result = build_context_with_cycle_summaries(messages, summaries, max_tokens=500, keep_last_cycles=2)
    assert len(result) > 0
    assert any("PEV cycles" in m.content for m in result if isinstance(m, SystemMessage))


def test_estimate_tokens():
    from langchain_core.messages import HumanMessage
    from langchain_core.messages import trim_messages
    messages = [HumanMessage(content="a" * 400)]
    result = trim_messages(messages, max_tokens=100, strategy="last", token_counter=len)
    assert len(result) <= 1
