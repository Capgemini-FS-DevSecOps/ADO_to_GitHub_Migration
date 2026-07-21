"""Integration test for graph execution — end-to-end graph flow, form submission, concurrent sessions."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from ado2gh.agents.migration_agent.graph import (
    _route_after_orchestrator,
    _route_after_planner,
    _route_after_executor,
    _route_after_validator,
    ALL_NODES,
)
from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
from ado2gh.agents.migration_agent.session_state import (
    SessionStateMachine,
    SessionState,
    InvalidTransitionError,
)


def test_graph_has_all_nodes():
    """Graph has five core PEV nodes."""
    assert len(ALL_NODES) == 5


def test_graph_flow_general_chat():
    """General chat: orchestrator→finalize."""
    state = {"should_return": False}
    assert _route_after_orchestrator(state) == "finalize"


def test_graph_flow_migration_action():
    """Migration action: orchestrator→planner→confirm→planner→executor→validator→orchestrator."""
    state = {"should_return": False, "start_pev": True}
    assert _route_after_orchestrator(state) == "planner"

    state["migration_plan"] = {"repos": [{"id": "A"}]}
    assert _route_after_planner(state) == "orchestrator"

    state["session"] = {"plan_approved": True}
    state["start_execution"] = True
    assert _route_after_orchestrator(state) == "planner"
    assert _route_after_planner(state) == "executor"

    state["executor_result"] = {"per_repo_results": [{"repo": "A"}]}
    assert _route_after_executor(state) == "validator"

    state["validation_result"] = {"passed": True}
    assert _route_after_validator(state) == "planner"

    state["planner_next"] = "orchestrator"
    assert _route_after_planner(state) == "orchestrator"


def test_graph_flow_form_pending():
    """Form pending: orchestrator→finalize (with pending_form)."""
    state = {"should_return": True}
    assert _route_after_orchestrator(state) == "finalize"


def test_concurrent_sessions_isolated():
    """Verify 10 concurrent sessions have isolated state."""
    import sqlite3
    from ado2gh.agents.migration_agent.session_store import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class MockDb:
        def _conn(self):
            return conn

    store = MigrationSessionStore(db=MockDb())
    sessions = []
    for i in range(10):
        s = store.create_session(profile_id=f"prof_{i}", model_id="gpt-4")
        sessions.append(s)

    for i, s in enumerate(sessions):
        store.update_session_status(s["session_id"], "executing")
        store.update_session(s["session_id"], iteration_count=i)

    for i, s in enumerate(sessions):
        loaded = store.get_session(s["session_id"])
        assert loaded["status"] == "executing"
        assert loaded["iteration_count"] == i


def test_cross_session_repo_lock_detection():
    """Verify repo locks prevent concurrent migration of same repo across sessions."""
    import sqlite3
    from ado2gh.agents.migration_agent.session_store import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class MockDb:
        def _conn(self):
            return conn

    store = MigrationSessionStore(db=MockDb())
    session1 = store.create_session(profile_id="prof1")
    session2 = store.create_session(profile_id="prof2")

    assert store.acquire_repo_lock(session1["session_id"], "Proj/RepoA") is True
    assert store.acquire_repo_lock(session2["session_id"], "Proj/RepoA") is False
    store.release_repo_lock(session1["session_id"], "Proj/RepoA")
    assert store.acquire_repo_lock(session2["session_id"], "Proj/RepoA") is True


def test_state_machine_direct_execution_from_thinking():
    """Approved plans may start execution without an intermediate planning state."""
    sm = SessionStateMachine(SessionState.THINKING)
    sm.transition(SessionState.EXECUTING)
    assert sm.state == SessionState.EXECUTING


def test_state_machine_drives_session_status():
    """Verify state machine transitions match graph node entry."""
    sm = SessionStateMachine()

    sm.transition(SessionState.THINKING)
    assert sm.state == SessionState.THINKING

    sm.transition(SessionState.PLANNING)
    assert sm.state == SessionState.PLANNING

    sm.transition(SessionState.EXECUTING)
    assert sm.state == SessionState.EXECUTING

    sm.transition(SessionState.VALIDATING)
    assert sm.state == SessionState.VALIDATING

    # validator → planner (all validation outcomes)
    sm.transition(SessionState.PLANNING)
    assert sm.state == SessionState.PLANNING


def test_state_machine_retry_transition():
    """Verify validator can transition back to planning for retry."""
    sm = SessionStateMachine(SessionState.VALIDATING)
    sm.transition(SessionState.PLANNING)
    assert sm.state == SessionState.PLANNING
