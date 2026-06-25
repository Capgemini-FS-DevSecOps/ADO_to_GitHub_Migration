"""Agentic platform mixin — assignments, audit events, remediation, dependencies.

Extracted from SQLiteStateDB to keep file under 800 lines.
Provides: insert_assignment, get_assignment, list_assignments, upsert_cohort_membership,
get_cohort_repos, insert_audit_event, list_audit_events, search_audit_events,
count_audit_events, list_audit_event_types, upsert_remediation_loop, get_remediation_loop,
get_dependency_edges, upsert_dependency_edge, has_repo_in_progress, is_repo_in_assignment_cohort.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


class AgenticPlatformMixin:
    """Agentic platform methods — mixed into SQLiteStateDB / PostgresStateDB."""

    def insert_assignment(
        self,
        id: str,
        profile_id: str,
        name: str,
        assignment_type: str,
        execution_phase: str,
        wave_number: int | None,
        status: str,
        created_by: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO migration_assignments
                (id, profile_id, name, assignment_type, execution_phase,
                 wave_number, status, created_by, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    id, profile_id, name, assignment_type, execution_phase,
                    wave_number, status, created_by, now,
                ),
            )

    def get_assignment(self, assignment_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM migration_assignments WHERE id=?", (assignment_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_assignments(self, profile_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM migration_assignments WHERE profile_id=? ORDER BY created_at",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_cohort_membership(
        self,
        assignment_id: str,
        profile_id: str,
        ado_project: str,
        ado_repo: str,
        gh_org: str,
        gh_repo: str,
        active: bool,
    ):
        with self._conn() as conn:
            if active:
                conn.execute(
                    "UPDATE cohort_membership SET active=0 "
                    "WHERE profile_id=? AND ado_project=? AND ado_repo=? AND active=1",
                    (profile_id, ado_project, ado_repo),
                )
            conn.execute(
                """
                INSERT INTO cohort_membership
                (assignment_id, profile_id, ado_project, ado_repo, gh_org, gh_repo, active)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    assignment_id, profile_id, ado_project, ado_repo,
                    gh_org, gh_repo, 1 if active else 0,
                ),
            )

    def get_cohort_repos(self, assignment_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ado_project, ado_repo, gh_org, gh_repo FROM cohort_membership "
                "WHERE assignment_id=? AND active=1",
                (assignment_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        assignment_id: str | None,
        payload_json: str,
        created_at: str,
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

    def upsert_remediation_loop(
        self, session_id: str, repo_key: str, retry_count: int,
        max_retries: int, status: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO remediation_loops
                (session_id, repo_key, retry_count, max_retries, status, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(session_id, repo_key) DO UPDATE SET
                    retry_count=excluded.retry_count,
                    max_retries=excluded.max_retries,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (session_id, repo_key, retry_count, max_retries, status, now),
            )

    def get_dependency_edges(self, profile_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM repo_dependency_edges WHERE profile_id=?",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_dependency_edge(
        self, profile_id: str, from_repo: str, to_repo: str,
        edge_type: str = "pipeline_resource",
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO repo_dependency_edges
                (profile_id, from_repo, to_repo, edge_type, discovered_at)
                VALUES (?,?,?,?,?)
                """,
                (profile_id, from_repo, to_repo, edge_type, now),
            )

    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM migrations WHERE ado_project=? AND ado_repo=? "
                "AND status='in_progress' LIMIT 1",
                (ado_project, ado_repo),
            ).fetchone()
        return row is not None

    def is_repo_in_assignment_cohort(
        self, assignment_id: str, ado_project: str, ado_repo: str,
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM cohort_membership WHERE assignment_id=? "
                "AND ado_project=? AND ado_repo=? AND active=1 LIMIT 1",
                (assignment_id, ado_project, ado_repo),
            ).fetchone()
        return row is not None

    def get_remediation_loop(self, session_id: str, repo_key: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM remediation_loops WHERE session_id=? AND repo_key=?",
                (session_id, repo_key),
            ).fetchone()
        return dict(row) if row else None
