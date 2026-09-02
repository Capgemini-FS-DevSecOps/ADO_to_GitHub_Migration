"""Per-repo locking to prevent concurrent migration runs on the same repository.

The pipeline runner is single-process (threaded), so an in-memory, thread-safe
lock table is sufficient. The interface is intentionally small so it can later be
backed by SQLite for multi-process deployments without changing call sites.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone


class RepoLockedException(RuntimeError):
    """Raised when a repo is already locked by a different run."""

    def __init__(self, repo_id: str, holder_run_id: str) -> None:
        self.repo_id = repo_id
        self.holder_run_id = holder_run_id
        super().__init__(f"migration in progress for {repo_id}")


@dataclass
class RepoLock:
    """A lock held on a single repository by one pipeline run."""

    repo_id: str
    run_id: str
    acquired_at: str


class RepoLockManager:
    """Thread-safe, process-local manager for per-repo migration locks."""

    def __init__(self) -> None:
        self._locks: dict[str, RepoLock] = {}
        self._lock = threading.Lock()

    def acquire(self, repo_id: str, run_id: str) -> RepoLock:
        """Acquire the lock for ``repo_id`` on behalf of ``run_id``.

        Re-acquiring a lock already held by the same ``run_id`` is idempotent.
        Raises :class:`RepoLockedException` if held by a different run.
        """
        with self._lock:
            existing = self._locks.get(repo_id)
            if existing is not None and existing.run_id != run_id:
                raise RepoLockedException(repo_id, existing.run_id)
            if existing is not None and existing.run_id == run_id:
                return existing
            lock = RepoLock(
                repo_id=repo_id,
                run_id=run_id,
                acquired_at=datetime.now(timezone.utc).isoformat(),
            )
            self._locks[repo_id] = lock
            return lock

    def release(self, repo_id: str, run_id: str) -> bool:
        """Release ``repo_id`` if held by ``run_id``. Returns True if released."""
        with self._lock:
            existing = self._locks.get(repo_id)
            if existing is not None and existing.run_id == run_id:
                del self._locks[repo_id]
                return True
            return False

    def release_all(self, run_id: str) -> int:
        """Release every lock held by ``run_id``. Returns the count released."""
        with self._lock:
            to_remove = [rid for rid, lk in self._locks.items() if lk.run_id == run_id]
            for rid in to_remove:
                del self._locks[rid]
            return len(to_remove)

    def is_locked(self, repo_id: str) -> bool:
        """Return True if ``repo_id`` currently holds a lock."""
        with self._lock:
            return repo_id in self._locks

    def holder(self, repo_id: str) -> str | None:
        """Return the run ID holding ``repo_id``, or None if unlocked."""
        with self._lock:
            lock = self._locks.get(repo_id)
            return lock.run_id if lock else None


# Process-wide shared lock manager used by the pipeline runner.
REPO_LOCK_MANAGER = RepoLockManager()
