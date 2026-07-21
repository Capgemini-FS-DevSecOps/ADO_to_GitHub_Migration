"""Rollback tracker — records GitHub resources created during a session.

Enables rollback on cancellation by tracking every resource the executor creates.
Only session-created resources are eligible for rollback.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


VALID_RESOURCE_TYPES = {
    "repo", "workflow", "secret", "environment", "issue", "wiki_page", "package",
}

VALID_ROLLBACK_STATUSES = {"eligible", "deleted", "failed"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS rollback_records (
    record_id         TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    resource_type     TEXT NOT NULL,
    resource_name     TEXT NOT NULL,
    github_org        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    correlation_id    TEXT NOT NULL,
    rollback_status   TEXT NOT NULL DEFAULT 'eligible'
);

CREATE INDEX IF NOT EXISTS idx_rollback_session
    ON rollback_records(session_id) WHERE rollback_status = 'eligible';
"""


class RollbackTracker:
    """Tracks GitHub resources created during a session for rollback."""

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

    def record_creation(
        self,
        session_id: str,
        resource_type: str,
        resource_name: str,
        github_org: str,
        correlation_id: str,
    ) -> str:
        """Record a GitHub resource creation. Returns the record ID."""
        if resource_type not in VALID_RESOURCE_TYPES:
            raise ValueError(f"Invalid resource_type: {resource_type}")
        rid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO rollback_records
                   (record_id, session_id, resource_type, resource_name,
                    github_org, created_at, correlation_id, rollback_status)
                   VALUES (?,?,?,?,?,?,?, 'eligible')""",
                (rid, session_id, resource_type, resource_name, github_org, now, correlation_id),
            )
        return rid

    def get_eligible(self, session_id: str) -> list[dict]:
        """Get all eligible rollback records for a session."""
        with self._db._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM rollback_records
                   WHERE session_id=? AND rollback_status='eligible'
                   ORDER BY created_at DESC""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_deleted(self, record_id: str) -> bool:
        """Mark a rollback record as deleted (rollback completed)."""
        with self._db._conn() as conn:
            cur = conn.execute(
                "UPDATE rollback_records SET rollback_status='deleted' WHERE record_id=?",
                (record_id,),
            )
            return cur.rowcount > 0

    def mark_failed(self, record_id: str) -> bool:
        """Mark a rollback record as failed (rollback attempt failed)."""
        with self._db._conn() as conn:
            cur = conn.execute(
                "UPDATE rollback_records SET rollback_status='failed' WHERE record_id=?",
                (record_id,),
            )
            return cur.rowcount > 0

    def get_session_records(self, session_id: str) -> list[dict]:
        """Get all rollback records for a session, regardless of status."""
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rollback_records WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
