"""Unit tests for RepoLockManager (feature 009, T014)."""
from __future__ import annotations

import pytest

from ado2gh.api.repo_lock import (
    RepoLock,
    RepoLockedException,
    RepoLockManager,
)


@pytest.fixture
def manager() -> RepoLockManager:
    return RepoLockManager()


class TestAcquire:
    def test_acquire_returns_lock(self, manager):
        lock = manager.acquire("Proj/RepoA", "run-1")
        assert isinstance(lock, RepoLock)
        assert lock.repo_id == "Proj/RepoA"
        assert lock.run_id == "run-1"
        assert lock.acquired_at  # ISO timestamp present
        assert manager.is_locked("Proj/RepoA")

    def test_acquire_same_run_is_idempotent(self, manager):
        first = manager.acquire("Proj/RepoA", "run-1")
        second = manager.acquire("Proj/RepoA", "run-1")
        assert first is second  # same lock object returned

    def test_acquire_different_run_raises(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        with pytest.raises(RepoLockedException) as exc_info:
            manager.acquire("Proj/RepoA", "run-2")
        assert exc_info.value.repo_id == "Proj/RepoA"
        assert exc_info.value.holder_run_id == "run-1"
        assert "migration in progress" in str(exc_info.value)

    def test_distinct_repos_independent(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        # Different repo, different run is fine.
        manager.acquire("Proj/RepoB", "run-2")
        assert manager.is_locked("Proj/RepoA")
        assert manager.is_locked("Proj/RepoB")


class TestRelease:
    def test_release_by_holder(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        assert manager.release("Proj/RepoA", "run-1") is True
        assert not manager.is_locked("Proj/RepoA")

    def test_release_by_non_holder_noop(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        assert manager.release("Proj/RepoA", "run-2") is False
        assert manager.is_locked("Proj/RepoA")

    def test_release_unlocked_noop(self, manager):
        assert manager.release("Proj/RepoA", "run-1") is False

    def test_release_allows_reacquire_by_other_run(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        manager.release("Proj/RepoA", "run-1")
        # After release, a different run can now acquire.
        lock = manager.acquire("Proj/RepoA", "run-2")
        assert lock.run_id == "run-2"


class TestReleaseAll:
    def test_release_all_for_run(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        manager.acquire("Proj/RepoB", "run-1")
        manager.acquire("Proj/RepoC", "run-2")
        released = manager.release_all("run-1")
        assert released == 2
        assert not manager.is_locked("Proj/RepoA")
        assert not manager.is_locked("Proj/RepoB")
        assert manager.is_locked("Proj/RepoC")  # other run's lock untouched

    def test_release_all_no_locks(self, manager):
        assert manager.release_all("run-x") == 0


class TestHolder:
    def test_holder_returns_run_id(self, manager):
        manager.acquire("Proj/RepoA", "run-1")
        assert manager.holder("Proj/RepoA") == "run-1"

    def test_holder_none_when_unlocked(self, manager):
        assert manager.holder("Proj/RepoA") is None
