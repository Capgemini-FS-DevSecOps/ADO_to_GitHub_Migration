"""SQLite audit-event methods, mixed into ``SQLiteStateDB``.

Kept separate so ``sqlite_db.py`` stays under the 800-line cap.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from ado2gh.state.audit_query import AuditEventFilters, build_audit_filters

if TYPE_CHECKING:
    import sqlite3

    class _SQLiteConnHost(Protocol):
        """Attribute ``AgenticPlatformMixin`` expects from ``SQLiteStateDB``."""

        def _conn(self) -> sqlite3.Connection: ...
else:
    _SQLiteConnHost = object


class AgenticPlatformMixin(_SQLiteConnHost):
    """Audit events and the in-progress guard; expects ``self._conn()``."""

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        payload_json: str,
    ) -> None:
        """Append one ``audit_events`` row; see :meth:`StateDBBase.insert_audit_event`."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                (id, event_type, profile_id, actor, payload_json, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (event_id, event_type, profile_id, actor, payload_json, created_at),
            )

    def list_audit_events(
        self, profile_id: str | None = None, limit: int = 100,
    ) -> list[dict]:
        """Return the newest audit events, restricted to one profile when given."""
        return self.search_audit_events(
            AuditEventFilters(profile_id=profile_id), limit=limit, offset=0,
        )

    def search_audit_events(
        self, filters: AuditEventFilters, *, limit: int = 20, offset: int = 0,
    ) -> list[dict]:
        """Return one page of audit events matching ``filters``, newest first."""
        clauses, params = build_audit_filters(filters)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM audit_events WHERE {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (*params, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def count_audit_events(self, filters: AuditEventFilters) -> int:
        """Return the number of audit events matching ``filters``."""
        clauses, params = build_audit_filters(filters)
        where = " AND ".join(clauses)
        sql = f"SELECT COUNT(*) AS c FROM audit_events WHERE {where}"
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    def list_audit_event_types(
        self,
        profile_id: str | None = None,
        limit: int = 200,
        actor: str | None = None,
    ) -> list[str]:
        """Return distinct event types, optionally restricted to a profile or actor."""
        clauses = ["1=1"]
        params: list[Any] = []
        if profile_id:
            clauses.append("profile_id=?")
            params.append(profile_id)
        if actor:
            clauses.append("actor=?")
            params.append(actor)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT DISTINCT event_type FROM audit_events WHERE {where} "
            "ORDER BY event_type LIMIT ?"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (*params, limit)).fetchall()
        return [str(r["event_type"]) for r in rows]

    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool:
        """Return whether any scope of a repository is currently ``in_progress``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM migrations WHERE ado_project=? AND ado_repo=? "
                "AND status='in_progress' LIMIT 1",
                (ado_project, ado_repo),
            ).fetchone()
        return row is not None
