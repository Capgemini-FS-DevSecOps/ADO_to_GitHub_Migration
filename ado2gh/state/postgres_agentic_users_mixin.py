"""Postgres agentic platform, audit, users, and live execution approvals mixin.

Extracted from PostgresStateDB to keep file under 800 lines.
"""
from __future__ import annotations

from typing import Any, Optional


class PostgresAgenticUsersMixin:
    """Agentic platform, audit events, platform users, auth sessions, live execution approvals."""

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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events
                    (id, event_type, profile_id, actor, assignment_id, payload_json, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
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
        where = " AND ".join(clauses).replace("?", "%s")
        sql = (
            f"SELECT * FROM audit_events WHERE {where} "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s"
        )
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, (*params, limit, offset))
                return [dict(r) for r in cur.fetchall()]

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
        where = " AND ".join(clauses).replace("?", "%s")
        sql = f"SELECT COUNT(*) AS c FROM audit_events WHERE {where}"
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_audit_event_types(
        self,
        profile_id: str | None = None,
        limit: int = 200,
        actor: str | None = None,
    ) -> list[str]:
        clauses = ["1=1"]
        params: list[Any] = []
        if profile_id:
            clauses.append("profile_id=%s")
            params.append(profile_id)
        if actor:
            clauses.append("actor=%s")
            params.append(actor)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT DISTINCT event_type FROM audit_events WHERE {where} "
            "ORDER BY event_type LIMIT %s"
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (*params, limit))
                return [str(row[0]) for row in cur.fetchall()]

    def count_platform_users(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM platform_users")
                row = cur.fetchone()
                return int(row[0]) if row else 0

    def create_platform_user(
        self, user_id: str, username: str, password_hash: str,
        role: str, display_name: str, created_at: str,
        status: str = "active",
    ):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO platform_users (id, username, password_hash, role, display_name, status, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (user_id, username, password_hash, role, display_name, status, created_at),
                )

    def get_platform_user_by_username(self, username: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM platform_users WHERE username=%s", (username,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_platform_user_by_id(self, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM platform_users WHERE id=%s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def list_platform_users(self) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, username, role, display_name, status, created_at FROM platform_users ORDER BY username",
                )
                return [dict(r) for r in cur.fetchall()]

    def update_platform_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
        display_name: str | None = None,
    ) -> bool:
        fields: list[str] = []
        values: list[Any] = []
        if role is not None:
            fields.append("role=%s")
            values.append(role)
        if status is not None:
            fields.append("status=%s")
            values.append(status)
        if display_name is not None:
            fields.append("display_name=%s")
            values.append(display_name)
        if not fields:
            return False
        values.append(user_id)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE platform_users SET {', '.join(fields)} WHERE id=%s",
                    values,
                )
                return cur.rowcount > 0

    def delete_auth_sessions_for_user(self, user_id: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE user_id=%s", (user_id,))

    def create_auth_session(self, token: str, user_id: str, expires_at: str, created_at: str):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_sessions (token, user_id, expires_at, created_at)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (token, user_id, expires_at, created_at),
                )

    def get_auth_session(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM auth_sessions WHERE token=%s", (token,))
                row = cur.fetchone()
                return dict(row) if row else None

    def delete_auth_session(self, token: str):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE token=%s", (token,))

    def create_live_execution_approval(
        self,
        approval_id: str,
        requester_user_id: str,
        requester_username: str,
        scope_type: str,
        scope_id: str,
        requested_at: str,
        assignment_id: str | None = None,
        profile_id: str | None = None,
        reason_request: str | None = None,
        context_json: str | None = None,
    ) -> dict:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_execution_approvals (
                        id, requester_user_id, requester_username, scope_type, scope_id,
                        assignment_id, profile_id, status, reason_request, context_json,
                        requested_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        approval_id, requester_user_id, requester_username,
                        scope_type, scope_id, assignment_id, profile_id,
                        "pending", reason_request, context_json, requested_at,
                    ),
                )
        return self.get_live_execution_approval(approval_id) or {}

    def get_live_execution_approval(self, approval_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM live_execution_approvals WHERE id=%s",
                    (approval_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_pending_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE scope_type=%s AND scope_id=%s AND status='pending'
                    ORDER BY requested_at DESC LIMIT 1
                    """,
                    (scope_type, scope_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_approved_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE scope_type=%s AND scope_id=%s AND status='approved'
                    ORDER BY decided_at DESC LIMIT 1
                    """,
                    (scope_type, scope_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def get_live_execution_approval_for_scope(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE scope_type=%s AND scope_id=%s
                    ORDER BY requested_at DESC LIMIT 1
                    """,
                    (scope_type, scope_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def list_live_execution_approvals(
        self, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                if status and status != "all":
                    cur.execute(
                        """
                        SELECT * FROM live_execution_approvals
                        WHERE status=%s ORDER BY requested_at DESC LIMIT %s
                        """,
                        (status, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM live_execution_approvals
                        ORDER BY requested_at DESC LIMIT %s
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_assignment(self, assignment_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migration_assignments WHERE id=%s",
                    (assignment_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def list_assignments(self, profile_id: str) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migration_assignments WHERE profile_id=%s ORDER BY created_at",
                    (profile_id,),
                )
                return [dict(r) for r in cur.fetchall()]

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
            with conn.cursor() as cur:
                if active:
                    cur.execute(
                        "UPDATE cohort_membership SET active=0 "
                        "WHERE profile_id=%s AND ado_project=%s AND ado_repo=%s AND active=1",
                        (profile_id, ado_project, ado_repo),
                    )
                cur.execute(
                    """
                    INSERT INTO cohort_membership
                    (assignment_id, profile_id, ado_project, ado_repo, gh_org, gh_repo, active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        assignment_id, profile_id, ado_project, ado_repo,
                        gh_org, gh_repo, 1 if active else 0,
                    ),
                )

    def get_cohort_repos(self, assignment_id: str) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT ado_project, ado_repo, gh_org, gh_repo FROM cohort_membership "
                    "WHERE assignment_id=%s AND active=1",
                    (assignment_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM migrations WHERE ado_project=%s AND ado_repo=%s "
                    "AND status='in_progress' LIMIT 1",
                    (ado_project, ado_repo),
                )
                row = cur.fetchone()
        return row is not None

    def is_repo_in_assignment_cohort(
        self, assignment_id: str, ado_project: str, ado_repo: str,
    ) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM cohort_membership WHERE assignment_id=%s "
                    "AND ado_project=%s AND ado_repo=%s AND active=1 LIMIT 1",
                    (assignment_id, ado_project, ado_repo),
                )
                row = cur.fetchone()
        return row is not None

    def decide_live_execution_approval(
        self,
        approval_id: str,
        status: str,
        approver_user_id: str,
        approver_username: str,
        reason_decision: str,
        decided_at: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT status FROM live_execution_approvals WHERE id=%s",
                    (approval_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if row["status"] != "pending":
                    return self.get_live_execution_approval(approval_id)
                cur.execute(
                    """
                    UPDATE live_execution_approvals
                    SET status=%s, approver_user_id=%s, approver_username=%s,
                        reason_decision=%s, decided_at=%s
                    WHERE id=%s
                    """,
                    (
                        status, approver_user_id, approver_username,
                        reason_decision, decided_at, approval_id,
                    ),
                )
        return self.get_live_execution_approval(approval_id)
