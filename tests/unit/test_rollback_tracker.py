"""Unit tests for rollback tracker (spec 011)."""
import pytest
from ado2gh.agents.rollback_tracker import RollbackTracker
from ado2gh.state.sqlite_db import SQLiteStateDB


@pytest.fixture
def tracker():
    db = SQLiteStateDB(":memory:")
    return RollbackTracker(db=db)


class TestRollbackTracker:
    def test_record_creation(self, tracker):
        rid = tracker.record_creation("sess-1", "repo", "org/repo-a", "myorg", "corr-1")
        assert rid is not None

    def test_get_eligible(self, tracker):
        tracker.record_creation("sess-1", "repo", "org/repo-a", "myorg", "corr-1")
        tracker.record_creation("sess-1", "workflow", ".github/workflows/ci.yml", "myorg", "corr-1")
        eligible = tracker.get_eligible("sess-1")
        assert len(eligible) == 2

    def test_mark_deleted(self, tracker):
        rid = tracker.record_creation("sess-1", "repo", "org/repo-a", "myorg", "corr-1")
        assert tracker.mark_deleted(rid) is True
        eligible = tracker.get_eligible("sess-1")
        assert len(eligible) == 0

    def test_mark_failed(self, tracker):
        rid = tracker.record_creation("sess-1", "repo", "org/repo-a", "myorg", "corr-1")
        assert tracker.mark_failed(rid) is True
        eligible = tracker.get_eligible("sess-1")
        assert len(eligible) == 0

    def test_invalid_resource_type(self, tracker):
        with pytest.raises(ValueError):
            tracker.record_creation("sess-1", "invalid_type", "name", "org", "corr-1")

    def test_get_session_records(self, tracker):
        tracker.record_creation("sess-1", "repo", "org/repo-a", "myorg", "corr-1")
        tracker.record_creation("sess-1", "secret", "MY_SECRET", "myorg", "corr-1")
        records = tracker.get_session_records("sess-1")
        assert len(records) == 2

    def test_isolation_between_sessions(self, tracker):
        tracker.record_creation("sess-1", "repo", "org/repo-a", "myorg", "corr-1")
        tracker.record_creation("sess-2", "repo", "org/repo-b", "myorg", "corr-2")
        assert len(tracker.get_eligible("sess-1")) == 1
        assert len(tracker.get_eligible("sess-2")) == 1
