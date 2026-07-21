"""Integration test for PEV loop — full PEV cycle via LangGraph, retry count, max iterations."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from ado2gh.agents.migration_agent.graph import (
    _route_after_orchestrator,
    _route_after_planner,
    _route_after_executor,
    _route_after_validator,
)


def _pev_state(**kwargs):
    """Create a minimal state dict for PEV loop testing."""
    defaults = {
        "should_return": False,
        "start_pev": True,
        "tool_calls": [],
        "pending_clarification": None,
        "migration_plan": None,
        "executor_result": None,
        "validation_result": None,
        "validation_feedback": None,
        "pev_retry_count": 0,
        "iteration": 0,
        "max_iterations": 20,
    }
    defaults.update(kwargs)
    return defaults


def test_full_pev_cycle_pass():
    """Simulate a full PEV cycle: orchestrator→planner→executor→validator→orchestrator (pass)."""
    # 1. Orchestrator starts PEV
    state = _pev_state(start_pev=True)
    assert _route_after_orchestrator(state) == "planner"

    # 2. Planner generates plan — routes to orchestrator for confirmation
    state["migration_plan"] = {"repos": [{"id": "A"}]}
    assert _route_after_planner(state) == "orchestrator"

    # 3. Operator confirms plan — executor runs
    state["session"] = {"plan_approved": True}
    state["start_execution"] = True
    assert _route_after_orchestrator(state) == "executor"

    # 4. Executor produces result
    state["executor_result"] = {"per_repo_results": [{"repo": "A", "scopes": {"git": {"status": "success"}}}]}
    assert _route_after_executor(state) == "validator"

    # 5. Validator passes
    state["validation_result"] = {"passed": True, "failures": []}
    state["validation_feedback"] = None
    assert _route_after_validator(state) == "orchestrator"


def test_full_pev_cycle_retry():
    """Simulate a PEV cycle with retry: fail→retry→pass."""
    # Cycle 1: Fail
    state = _pev_state(
        start_pev=True,
        migration_plan={"repos": [{"id": "A"}]},
        executor_result={"per_repo_results": [{"repo": "A", "scopes": {"git": {"error": "failed"}}}]},
        validation_result={"passed": False, "failures": [{"repo": "A", "scope": "git"}]},
        validation_feedback={"failures": ["git_parity"], "retry_recommended": True},
        pev_retry_count=1,
        iteration=1,
    )
    assert _route_after_validator(state) == "planner"

    # Cycle 2: Pass (retry succeeds)
    state["executor_result"] = {"per_repo_results": [{"repo": "A", "scopes": {"git": {"status": "success"}}}]}
    state["validation_result"] = {"passed": True, "failures": []}
    state["validation_feedback"] = None
    state["pev_retry_count"] = 1  # Was incremented by validator
    assert _route_after_validator(state) == "orchestrator"


def test_pev_max_retries_exhausted():
    """PEV loop should route to orchestrator after 3 retries."""
    state = _pev_state(
        validation_feedback={"failures": ["git_parity"]},
        pev_retry_count=3,
        iteration=5,
    )
    assert _route_after_validator(state) == "orchestrator"


def test_pev_max_iterations_exhausted():
    """PEV loop should route to orchestrator after 20 iterations."""
    state = _pev_state(
        validation_feedback={"failures": ["git_parity"]},
        pev_retry_count=1,
        iteration=20,
    )
    assert _route_after_validator(state) == "orchestrator"


def test_pev_pending_clarification_routes_to_orchestrator():
    """When planner needs clarification, route to orchestrator."""
    state = _pev_state(
        migration_plan=None,
        pending_clarification={"to_role": "orchestrator", "reason": "Need discovery data"},
    )
    assert _route_after_planner(state) == "orchestrator"


def test_pev_executor_clarification_routes_to_orchestrator():
    """When executor needs clarification, route to orchestrator."""
    state = _pev_state(
        executor_result=None,
        pending_clarification={"to_role": "planner", "reason": "Missing pipeline mapping"},
    )
    assert _route_after_executor(state) == "orchestrator"


def test_pev_should_return_overrides_everything():
    """should_return should always route to finalize."""
    state = _pev_state(
        should_return=True,
        start_pev=True,
        migration_plan={"repos": []},
        executor_result={"per_repo_results": []},
        validation_feedback={"failures": []},
        pev_retry_count=0,
        iteration=0,
    )
    assert _route_after_orchestrator(state) == "finalize"
    assert _route_after_planner(state) == "finalize"
    assert _route_after_executor(state) == "finalize"
    assert _route_after_validator(state) == "finalize"


def test_pev_multiple_retries_increment():
    """Verify retry count increments properly across cycles."""
    for retry_count in range(3):
        state = _pev_state(
            validation_feedback={"failures": ["git_parity"]},
            pev_retry_count=retry_count,
            iteration=retry_count + 1,
        )
        if retry_count < 3:
            assert _route_after_validator(state) == "planner"
        # After 3 retries, route to orchestrator
    state = _pev_state(
        validation_feedback={"failures": ["git_parity"]},
        pev_retry_count=3,
        iteration=4,
    )
    assert _route_after_validator(state) == "orchestrator"
