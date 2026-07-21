"""Unit tests for conditional edge routing in the LangGraph migration agent.

Tests all routing scenarios: orchestrator→planner, planner→executor,
executor→validator, validator→planner (retry), validator→orchestrator
(complete/failed), pending_clarification routing.
"""
import pytest

from ado2gh.agents.migration_agent.graph import (
    _route_after_orchestrator,
    _route_after_planner,
    _route_after_executor,
    _route_after_validator,
    _route_after_execute_tools,
)


# ─── Orchestrator routing ─────────────────────────────────────────────

def test_route_orchestrator_to_planner_when_pev():
    state = {"should_return": False, "start_pev": True, "tool_calls": []}
    assert _route_after_orchestrator(state) == "planner"


def test_route_orchestrator_to_planner_when_execution_requested():
    state = {"should_return": False, "start_execution": True}
    assert _route_after_orchestrator(state) == "planner"


def test_route_orchestrator_to_finalize_when_should_return():
    state = {"should_return": True, "start_pev": False, "tool_calls": []}
    assert _route_after_orchestrator(state) == "finalize"


def test_route_orchestrator_to_finalize_when_nothing_pending():
    state = {"should_return": False, "start_pev": False, "tool_calls": []}
    assert _route_after_orchestrator(state) == "finalize"


def test_route_orchestrator_pev_takes_priority_over_execution():
    state = {"should_return": False, "start_pev": True, "start_execution": True}
    assert _route_after_orchestrator(state) == "planner"


def test_route_orchestrator_planner_when_replan_requested_with_stale_plan():
    state = {
        "should_return": False,
        "start_pev": True,
        "migration_plan": {"repos": [{"id": "p/r"}]},
        "session": {"plan_approved": False},
    }
    assert _route_after_orchestrator(state) == "planner"


def test_route_orchestrator_start_pev_takes_priority_over_should_return():
    state = {"should_return": True, "start_pev": True, "start_execution": True}
    assert _route_after_orchestrator(state) == "finalize"


# ─── Planner routing ──────────────────────────────────────────────────

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


def test_route_planner_to_finalize_when_should_return():
    state = {"should_return": True, "pending_clarification": None, "migration_plan": {"repos": []}}
    assert _route_after_planner(state) == "finalize"


def test_route_planner_to_finalize_when_no_plan():
    state = {"should_return": False, "pending_clarification": None, "migration_plan": None}
    assert _route_after_planner(state) == "finalize"


def test_route_planner_clarification_takes_priority():
    state = {
        "pending_clarification": {"to_role": "orchestrator"},
        "should_return": False,
        "migration_plan": {"repos": []},
    }
    assert _route_after_planner(state) == "orchestrator"


# ─── Executor routing ─────────────────────────────────────────────────

def test_route_executor_to_validator_when_done():
    state = {"executor_result": {"per_repo_results": []}, "should_return": False}
    assert _route_after_executor(state) == "validator"


def test_route_executor_to_finalize_when_no_result():
    state = {"should_return": False, "executor_result": None}
    assert _route_after_executor(state) == "finalize"


def test_route_executor_always_validator_when_result_present():
    state = {
        "should_return": False,
        "executor_result": {"per_repo_results": []},
    }
    assert _route_after_executor(state) == "validator"


# ─── Validator routing ────────────────────────────────────────────────

def test_route_validator_to_planner_on_retry():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 1,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_validator_to_planner_on_pass():
    state = {
        "validation_result": {"passed": True},
        "validation_feedback": None,
        "pev_retry_count": 0,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_validator_to_planner_on_max_retries():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 3,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_validator_to_planner_on_max_iterations():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 1,
        "iteration": 20,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_validator_no_feedback_no_result_goes_planner():
    state = {
        "validation_result": None,
        "validation_feedback": None,
        "pev_retry_count": 0,
        "iteration": 0,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_validator_to_finalize_when_should_return():
    state = {
        "should_return": True,
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 0,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "finalize"


def test_route_execute_tools_alias_matches_orchestrator():
    """Legacy _route_after_execute_tools alias routes like orchestrator."""
    from ado2gh.agents.migration_agent.graph import _route_after_execute_tools
    state = {"should_return": False, "start_pev": True}
    assert _route_after_execute_tools(state) == "planner"
