"""Unit tests for repo lock store (spec 011)."""
import pytest
from ado2gh.agents.repo_lock_store import RepoLockStore
from ado2gh.state.sqlite_db import SQLiteStateDB


@pytest.fixture
def store():
    db = SQLiteStateDB(":memory:")
    return RepoLockStore(db=db)


class TestRepoLockStore:
    def test_acquire_and_is_locked(self, store):
        assert store.acquire("Proj/RepoA", "sess-1") is True
        assert store.is_locked("Proj/RepoA") is True

    def test_acquire_already_locked(self, store):
        store.acquire("Proj/RepoA", "sess-1")
        assert store.acquire("Proj/RepoA", "sess-2") is False

    def test_release(self, store):
        store.acquire("Proj/RepoA", "sess-1")
        assert store.release("Proj/RepoA", "sess-1") is True
        assert store.is_locked("Proj/RepoA") is False

    def test_release_not_holder(self, store):
        store.acquire("Proj/RepoA", "sess-1")
        assert store.release("Proj/RepoA", "sess-2") is False

    def test_holder(self, store):
        store.acquire("Proj/RepoA", "sess-1")
        assert store.holder("Proj/RepoA") == "sess-1"
        assert store.holder("Proj/RepoB") is None

    def test_cleanup_stale(self, store):
        store.acquire("Proj/RepoA", "sess-1")
        cleaned = store.cleanup_stale(max_age_hours=0)
        assert cleaned >= 1
        assert store.is_locked("Proj/RepoA") is False
