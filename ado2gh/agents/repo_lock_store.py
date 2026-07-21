"""Persistent repo-level lock store.

Prevents concurrent migration of the same repository across sessions.
Stale locks are cleaned up on startup with audit logging.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


SCHEMA = """
CREATE TABLE IF NOT EXISTS repo_locks (
    lock_id           TEXT PRIMARY KEY,
    repo_key          TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    acquired_at       TEXT NOT NULL,
    released_at       TEXT,
    lock_state        TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_repo_locks_active
    ON repo_locks(repo_key) WHERE lock_state = 'active';
"""


class RepoLockStore:
    """DB-backed repo-level locks for migration sessions."""

    def __init__(self, db=None) -> None:
        if db is None:
            from ado2gh.state.factory import create_state_db
            db = create_state_db()
        self._db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if hasattr(self._db, "_conn"):
            with self._db._conn() as conn:
                conn.executescript(SCHEMA)

    def acquire(self, repo_key: str, session_id: str) -> bool:
        """Try to acquire a lock. Returns True if acquired, False if already locked."""
        if self.is_locked(repo_key):
            return False
        lid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO repo_locks (lock_id, repo_key, session_id, acquired_at, lock_state)
                   VALUES (?,?,?,?, 'active')""",
                (lid, repo_key, session_id, now),
            )
        return True

    def release(self, repo_key: str, session_id: str) -> bool:
        """Release a lock held by the given session. Returns True if released."""
        now = _now()
        with self._db._conn() as conn:
            cur = conn.execute(
                """UPDATE repo_locks SET lock_state='released', released_at=?
                   WHERE repo_key=? AND session_id=? AND lock_state='active'""",
                (now, repo_key, session_id),
            )
            return cur.rowcount > 0

    def is_locked(self, repo_key: str) -> bool:
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM repo_locks WHERE repo_key=? AND lock_state='active' LIMIT 1",
                (repo_key,),
            ).fetchone()
        return row is not None

    def holder(self, repo_key: str) -> Optional[str]:
        """Return the session_id holding the active lock, or None."""
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM repo_locks WHERE repo_key=? AND lock_state='active' LIMIT 1",
                (repo_key,),
            ).fetchone()
        return row["session_id"] if row else None

    def cleanup_stale(self, max_age_hours: int = 24) -> int:
        """Mark locks older than max_age_hours as stale. Returns count cleaned."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with self._db._conn() as conn:
            cur = conn.execute(
                """UPDATE repo_locks SET lock_state='stale'
                   WHERE lock_state='active' AND acquired_at < ?""",
                (cutoff,),
            )
            return cur.rowcount
