"""Agentic platform mixin — audit events.

Extracted from SQLiteStateDB to keep file under 800 lines.
Provides: insert_audit_event, list_audit_events, search_audit_events,
count_audit_events, list_audit_event_types, has_repo_in_progress.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


class AgenticPlatformMixin:
    """Agentic platform methods — mixed into SQLiteStateDB / PostgresStateDB."""

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        payload_json: str,
        created_at: str,
        assignment_id: str | None = None,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                (id, event_type, profile_id, actor, assignment_id, payload_json, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    event_id, event_type, profile_id, actor,
                    assignment_id, payload_json, created_at,
                ),
            )

    def list_audit_events(
        self, profile_id: str | None = None, limit: int = 100,
    ) -> list[dict]:
        return self.search_audit_events(
            profile_id=profile_id, limit=limit, offset=0,
        )

    def search_audit_events(
        self,
        *,
        profile_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        actor: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        from ado2gh.state.audit_query import AuditEventFilters, build_audit_filters

        filters = AuditEventFilters(
            profile_id=profile_id,
            actor=actor,
            event_type=event_type,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        clauses, params = build_audit_filters(filters)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM audit_events WHERE {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (*params, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def count_audit_events(
        self,
        *,
        profile_id: str | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> int:
        from ado2gh.state.audit_query import AuditEventFilters, build_audit_filters

        filters = AuditEventFilters(
            profile_id=profile_id,
            actor=actor,
            event_type=event_type,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
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
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM migrations WHERE ado_project=? AND ado_repo=? "
                "AND status='in_progress' LIMIT 1",
                (ado_project, ado_repo),
            ).fetchone()
        return row is not None

