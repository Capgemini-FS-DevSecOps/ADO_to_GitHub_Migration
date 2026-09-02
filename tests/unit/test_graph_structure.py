"""Unit tests for LangGraph structure — verify nodes, edges, recursion limit."""
import pytest

from ado2gh.agents.migration_agent.graph import (
    ALL_NODES,
    NODE_ORCHESTRATOR,
    NODE_PLANNER,
    NODE_EXECUTOR,
    NODE_VALIDATOR,
    NODE_FINALIZE,
    NODE_HUMAN_INPUT,
    NODE_CLASSIFY_INTENT,
    NODE_EXECUTE_TOOLS,
    _route_after_orchestrator,
    _route_after_planner,
    _route_after_executor,
    _route_after_validator,
    GRAPH_RECURSION_LIMIT,
)
from ado2gh.agents.migration_agent.constants import GRAPH_RECURSION_LIMIT as CONST_LIMIT


def test_all_nodes_present():
    """Core graph has six nodes: orchestrator, planner, executor, validator, human_input, finalize."""
    expected = {
        NODE_ORCHESTRATOR,
        NODE_PLANNER,
        NODE_EXECUTOR,
        NODE_VALIDATOR,
        NODE_HUMAN_INPUT,
        NODE_FINALIZE,
    }
    assert set(ALL_NODES) == expected


def test_legacy_node_aliases_map_to_orchestrator():
    assert NODE_CLASSIFY_INTENT == NODE_ORCHESTRATOR
    assert NODE_EXECUTE_TOOLS == NODE_ORCHESTRATOR


def test_recursion_limit_at_least_40():
    assert GRAPH_RECURSION_LIMIT >= 40
    assert CONST_LIMIT >= 40


def test_route_after_orchestrator_finalize():
    state = {"should_return": True}
    assert _route_after_orchestrator(state) == "finalize"


def test_route_after_orchestrator_planner_on_pev():
    state = {"should_return": False, "start_pev": True}
    assert _route_after_orchestrator(state) == "planner"


def test_route_after_orchestrator_planner_on_execution():
    state = {"should_return": False, "start_execution": True}
    assert _route_after_orchestrator(state) == "planner"


def test_route_after_orchestrator_default_finalize():
    state = {"should_return": False}
    assert _route_after_orchestrator(state) == "finalize"


def test_route_after_planner_executor():
    state = {
        "migration_plan": {"repos": []},
        "pending_clarification": None,
        "should_return": False,
        "session": {"plan_approved": True},
    }
    assert _route_after_planner(state) == "executor"


def test_route_after_planner_orchestrator_for_confirmation():
    state = {
        "migration_plan": {"repos": []},
        "pending_clarification": None,
        "should_return": False,
        "session": {"plan_approved": False},
    }
    assert _route_after_planner(state) == "orchestrator"


def test_route_after_planner_clarification():
    state = {"pending_clarification": {"to_role": "orchestrator"}, "should_return": False}
    assert _route_after_planner(state) == "orchestrator"


def test_route_after_planner_finalize():
    state = {"should_return": True}
    assert _route_after_planner(state) == "finalize"


def test_route_after_executor_validator():
    state = {"executor_result": {"per_repo_results": []}, "should_return": False}
    assert _route_after_executor(state) == "validator"


def test_route_after_executor_finalize_without_result():
    state = {"should_return": False}
    assert _route_after_executor(state) == "finalize"


def test_route_after_validator_retry():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 1,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "planner"


def test_route_after_validator_orchestrator_on_pass():
    state = {
        "validation_result": {"passed": True},
        "validation_feedback": None,
        "pev_retry_count": 0,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "orchestrator"


def test_route_after_validator_max_retries():
    state = {
        "validation_feedback": {"failures": ["git_parity"]},
        "pev_retry_count": 3,
        "iteration": 5,
        "max_iterations": 20,
    }
    assert _route_after_validator(state) == "orchestrator"


def test_route_after_planner_orchestrator_after_validation_complete():
    state = {"planner_next": "orchestrator", "should_return": False}
    assert _route_after_planner(state) == "orchestrator"


def test_route_after_planner_executor_after_validation_queue():
    state = {"planner_next": "executor", "should_return": False}
    assert _route_after_planner(state) == "executor"
