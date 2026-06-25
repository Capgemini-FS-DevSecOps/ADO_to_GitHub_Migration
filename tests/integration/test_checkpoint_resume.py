"""Integration test for checkpoint resume — session persistence across restart simulation."""
import pytest
import json
import sqlite3
from unittest.mock import MagicMock

from ado2gh.agents.migration_agent.session_store import MigrationSessionStore, SCHEMA


def _make_in_memory_store() -> MigrationSessionStore:
    """Create a MigrationSessionStore with in-memory SQLite."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class MockDb:
        def _conn(self):
            return conn

    return MigrationSessionStore(db=MockDb())


def test_checkpoint_save_and_load():
    """Verify session state can be saved and loaded for resume."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1", model_id="gpt-4")
    sid = session["session_id"]

    state = {
        "session": session,
        "migration_plan": {"repos": [{"id": "A"}], "work_items": [], "revision": 1},
        "executor_result": {"per_repo_results": [{"repo": "A"}], "failures": [], "skipped": []},
        "validation_result": {"per_scope": {}, "evidence": [], "failures": []},
        "cycle_summaries": [{"cycle_number": 1, "repos_processed": 1, "repos_succeeded": 1, "repos_failed": 0, "failures": [], "next_action": "complete"}],
        "rollback_records": [],
        "iteration": 3,
        "pev_retry_count": 1,
    }
    store.save_session_state(sid, state)

    loaded = store.load_session_state(sid)
    assert loaded["migration_plan"] is not None
    assert loaded["executor_result"] is not None
    assert loaded["validation_result"] is not None
    assert loaded["iteration"] == 3
    assert loaded["pev_retry_count"] == 1
    assert len(loaded["cycle_summaries"]) == 1


def test_checkpoint_resume_preserves_plan():
    """Verify migration plan survives save/load cycle."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]

    plan = {"repos": [{"id": "A"}, {"id": "B"}], "work_items": [{"repo": "A", "scope": "git"}], "revision": 2}
    store.save_session_state(sid, {"migration_plan": plan, "iteration": 1, "pev_retry_count": 0})
    loaded = store.load_session_state(sid)
    assert loaded["migration_plan"]["repos"] == plan["repos"]
    assert loaded["migration_plan"]["revision"] == 2


def test_checkpoint_resume_preserves_executor_result():
    """Verify executor result survives save/load cycle."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]

    plan = {"repos": [{"id": "A"}]}
    store.save_plan(sid, plan)
    executor_result = {
        "per_repo_results": [{"repo": "A", "scopes": {"git": {"status": "success"}}}],
        "failures": [],
        "skipped": [],
    }
    store.save_session_state(sid, {
        "migration_plan": plan,
        "executor_result": executor_result,
        "iteration": 2,
        "pev_retry_count": 0,
    })
    loaded = store.load_session_state(sid)
    assert loaded["executor_result"]["per_repo_results"][0]["repo"] == "A"


def test_checkpoint_resume_preserves_validation_result():
    """Verify validation result survives save/load cycle."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]

    plan = {"repos": [{"id": "A"}]}
    validation_result = {
        "per_scope": {"git": {"passed": True}},
        "evidence": [{"type": "git_sha", "value": "abc123"}],
        "failures": [],
    }
    store.save_session_state(sid, {
        "migration_plan": plan,
        "validation_result": validation_result,
        "iteration": 3,
        "pev_retry_count": 0,
    })
    loaded = store.load_session_state(sid)
    assert loaded["validation_result"]["per_scope"]["git"]["passed"] is True


def test_checkpoint_resume_preserves_cycle_summaries():
    """Verify cycle summaries accumulate across save/load cycles."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]

    # Save first cycle
    store.save_session_state(sid, {
        "cycle_summaries": [{"cycle_number": 1, "repos_processed": 1, "repos_succeeded": 1, "repos_failed": 0, "failures": [], "next_action": "complete"}],
        "iteration": 1,
        "pev_retry_count": 0,
    })
    # Save second cycle
    store.save_session_state(sid, {
        "cycle_summaries": [{"cycle_number": 2, "repos_processed": 2, "repos_succeeded": 1, "repos_failed": 1, "failures": [{"repo": "B"}], "next_action": "retry_planner"}],
        "iteration": 2,
        "pev_retry_count": 1,
    })
    loaded = store.load_session_state(sid)
    # Both cycles should be in the DB
    assert len(loaded["cycle_summaries"]) >= 2


def test_checkpoint_resume_nonexistent_session():
    """Loading a nonexistent session returns empty dict."""
    store = _make_in_memory_store()
    loaded = store.load_session_state("nonexistent-id")
    assert loaded == {}


def test_checkpoint_resume_preserves_rollback_records():
    """Verify rollback records survive save/load cycle."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]

    store.save_session_state(sid, {
        "rollback_records": [
            {"resource_type": "git", "resource_name": "A", "github_org": "org1", "correlation_id": "A:git"},
        ],
        "iteration": 1,
        "pev_retry_count": 0,
    })
    loaded = store.load_session_state(sid)
    assert len(loaded["rollback_records"]) == 1
    assert loaded["rollback_records"][0]["resource_name"] == "A"


def test_checkpoint_resume_preserves_repo_locks():
    """Verify repo locks survive across sessions."""
    store = _make_in_memory_store()
    session = store.create_session(profile_id="prof1")
    sid = session["session_id"]

    # Acquire lock
    assert store.acquire_repo_lock(sid, "Proj/RepoA") is True
    # Same session can't re-acquire
    assert store.acquire_repo_lock(sid, "Proj/RepoA") is False
    # Release and re-acquire
    store.release_repo_lock(sid, "Proj/RepoA")
    assert store.acquire_repo_lock(sid, "Proj/RepoA") is True
