"""Unit tests for session persistence — save/load state, resume, repo locks."""
import pytest
import json
from unittest.mock import MagicMock, patch

from ado2gh.agents.migration_agent.session_store import MigrationSessionStore


def _make_mock_db():
    """Create an in-memory SQLite DB for testing."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(MigrationSessionStore.__module__ and __import__(
        "ado2gh.agents.migration_agent.session_store", fromlist=["SCHEMA"]
    ).SCHEMA)

    class MockDb:
        def _conn(self):
            return conn

    return MockDb()


@pytest.fixture
def store():
    db = _make_mock_db()
    return MigrationSessionStore(db=db)


def test_create_and_get_session(store):
    session = store.create_session(profile_id="prof1", model_id="gpt-4", dry_run=True)
    sid = session["session_id"]
    assert sid is not None
    loaded = store.get_session(sid)
    assert loaded is not None
    assert loaded["profile_id"] == "prof1"
    assert loaded["dry_run"] is True
    assert loaded["status"] == "idle"


def test_update_session_status(store):
    session = store.create_session(profile_id="prof1")
    store.update_session_status(session["session_id"], "executing")
    loaded = store.get_session(session["session_id"])
    assert loaded["status"] == "executing"


def test_update_session_fields(store):
    session = store.create_session(profile_id="prof1")
    plan = {"repos": [{"id": "A"}], "revision": 1}
    store.update_session(
        session["session_id"],
        migration_plan=plan,
        iteration_count=3,
        pev_retry_count=1,
        dry_run=False,
    )
    loaded = store.get_session(session["session_id"])
    assert loaded["migration_plan"] == plan
    assert loaded["dry_run"] is False


def test_list_sessions(store):
    store.create_session(profile_id="prof1")
    store.create_session(profile_id="prof2")
    all_sessions = store.list_sessions()
    assert len(all_sessions) >= 2
    prof1 = store.list_sessions(profile_id="prof1")
    assert all(s["profile_id"] == "prof1" for s in prof1)


def test_delete_session(store):
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]
    store.delete_session(sid)
    assert store.get_session(sid) is None


def test_add_and_get_messages(store):
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]
    store.add_message(sid, "orchestrator", "planner", "plan_ready", {"plan_id": "p1"})
    msgs = store.get_messages(sid)
    assert len(msgs) == 1
    assert msgs[0]["from_role"] == "orchestrator"


def test_save_and_get_plan(store):
    session = store.create_session(profile_id="prof1")
    plan = {"repos": [{"id": "A"}], "work_items": [], "dry_run": True}
    pid = store.save_plan(session["session_id"], plan)
    assert pid is not None


def test_save_cycle_summary(store):
    session = store.create_session(profile_id="prof1")
    summary = {"cycle_number": 1, "repos_processed": 3, "repos_succeeded": 2, "repos_failed": 1, "failures": [], "next_action": "retry_planner"}
    cid = store.save_cycle_summary(session["session_id"], summary)
    assert cid is not None
    summaries = store.get_cycle_summaries(session["session_id"])
    assert len(summaries) == 1
    assert summaries[0]["cycle_number"] == 1


def test_save_guardrail_decision(store):
    session = store.create_session(profile_id="prof1")
    decision = {"agent_role": "executor", "tool_name": "call_accelerator", "operation_type": "call_accelerator", "target_resource": "A", "decision": "allow", "reason": "Plan approved"}
    did = store.save_guardrail_decision(session["session_id"], decision)
    assert did is not None


def test_save_and_get_rollback_record(store):
    session = store.create_session(profile_id="prof1")
    record = {"resource_type": "git", "resource_name": "A", "github_org": "org1", "correlation_id": "A:git"}
    rid = store.save_rollback_record(session["session_id"], record)
    assert rid is not None
    records = store.get_rollback_records(session["session_id"])
    assert len(records) == 1


def test_repo_lock_acquire_and_release(store):
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]
    assert store.acquire_repo_lock(sid, "Proj/RepoA") is True
    assert store.acquire_repo_lock(sid, "Proj/RepoA") is False  # Already locked
    store.release_repo_lock(sid, "Proj/RepoA")
    assert store.acquire_repo_lock(sid, "Proj/RepoA") is True  # Can re-lock


def test_release_all_locks(store):
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]
    store.acquire_repo_lock(sid, "A")
    store.acquire_repo_lock(sid, "B")
    store.release_all_locks(sid)
    assert store.acquire_repo_lock(sid, "A") is True
    assert store.acquire_repo_lock(sid, "B") is True


def test_save_executor_result(store):
    session = store.create_session(profile_id="prof1")
    plan = {"repos": [{"id": "A"}]}
    pid = store.save_plan(session["session_id"], plan)
    result = {"per_repo_results": [{"repo": "A"}], "failures": [], "skipped": []}
    rid = store.save_executor_result(session["session_id"], pid, result)
    assert rid is not None
    results = store.get_executor_results(session["session_id"])
    assert len(results) == 1


def test_save_validation_result(store):
    session = store.create_session(profile_id="prof1")
    plan = {"repos": [{"id": "A"}]}
    pid = store.save_plan(session["session_id"], plan)
    result = {"per_scope": {"git": {}}, "evidence": [], "failures": []}
    vid = store.save_validation_result(session["session_id"], pid, result)
    assert vid is not None
    results = store.get_validation_results(session["session_id"])
    assert len(results) == 1


def test_save_and_load_session_state(store):
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]
    state = {
        "session": session,
        "migration_plan": {"repos": [{"id": "A"}], "work_items": [], "revision": 1},
        "executor_result": {"per_repo_results": [{"repo": "A"}], "failures": [], "skipped": []},
        "validation_result": {"per_scope": {}, "evidence": [], "failures": []},
        "cycle_summaries": [{"cycle_number": 1, "repos_processed": 1, "repos_succeeded": 1, "repos_failed": 0, "failures": [], "next_action": "complete"}],
        "rollback_records": [{"resource_type": "git", "resource_name": "A", "github_org": "org1", "correlation_id": "A:git"}],
        "iteration": 3,
        "pev_retry_count": 1,
    }
    store.save_session_state(sid, state)
    loaded = store.load_session_state(sid)
    assert loaded["migration_plan"] is not None
    assert loaded["executor_result"] is not None
    assert loaded["validation_result"] is not None
    assert len(loaded["cycle_summaries"]) == 1
    assert loaded["iteration"] == 3
    assert loaded["pev_retry_count"] == 1


def test_load_session_state_nonexistent(store):
    loaded = store.load_session_state("nonexistent")
    assert loaded == {}


def test_add_message_to_session(store):
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]
    store.add_message_to_session(sid, "user", "hello", kind="text")
    store.add_message_to_session(sid, "assistant", "hi", kind="message", subagent="orchestrator")
    loaded = store.get_session(sid)
    assert len(loaded["messages"]) == 2
    assert loaded["messages"][0]["role"] == "user"
    assert loaded["messages"][1]["subagent"] == "orchestrator"
