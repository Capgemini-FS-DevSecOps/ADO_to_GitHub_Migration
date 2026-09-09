"""SQLite platform-user, session and live-approval methods, mixed into ``SQLiteStateDB``.

Kept separate so ``sqlite_db.py`` stays under the 800-line cap. Password
hashes and session tokens are stored as opaque strings and never logged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ado2gh.auth.models import PlatformUser


class PlatformUsersMixin:
    """Platform users, auth sessions and live execution approvals; expects ``self._conn()``."""

    def count_platform_users(self) -> int:
        """Return the number of platform users."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM platform_users").fetchone()
        return int(row["c"]) if row else 0

    def create_platform_user(
        self,
        user: PlatformUser,
        *,
        password_hash: str,
        status: str,
        created_at: str,
    ) -> None:
        """Insert one ``platform_users`` row; see :meth:`StateDBBase.create_platform_user`."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO platform_users (id, username, password_hash, role, display_name, status, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    user.id, user.username, password_hash, user.role.value,
                    user.display_name, status, created_at,
                ),
            )

    def get_platform_user_by_username(self, username: str) -> dict | None:
        """Return the user row with this username, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM platform_users WHERE username=?",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def get_platform_user_by_id(self, user_id: str) -> dict | None:
        """Return the user row with this id, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM platform_users WHERE id=?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_platform_users(self) -> list[dict]:
        """Return every user ordered by username, without password hashes."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, username, role, display_name, status, created_at FROM platform_users ORDER BY username",
            ).fetchall()
        return [dict(r) for r in rows]

    def update_platform_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
        display_name: str | None = None,
    ) -> bool:
        """Update the given fields of a user; see :meth:`StateDBBase.update_platform_user`.

        Returns:
            ``True`` when a row changed; ``False`` when no field was given or the
            user does not exist.
        """
        fields: list[str] = []
        values: list[Any] = []
        if role is not None:
            fields.append("role=?")
            values.append(role)
        if status is not None:
            fields.append("status=?")
            values.append(status)
        if display_name is not None:
            fields.append("display_name=?")
            values.append(display_name)
        if not fields:
            return False
        values.append(user_id)
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE platform_users SET {', '.join(fields)} WHERE id=?",
                values,
            )
        return cur.rowcount > 0

    def delete_auth_sessions_for_user(self, user_id: str) -> None:
        """Delete every session of a user."""
        with self._conn() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))

    def create_auth_session(
        self, token: str, user_id: str, expires_at: str, created_at: str,
    ) -> None:
        """Insert one ``auth_sessions`` row keyed by the session token."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (token, user_id, expires_at, created_at)
                VALUES (?,?,?,?)
                """,
                (token, user_id, expires_at, created_at),
            )

    def get_auth_session(self, token: str) -> dict | None:
        """Return the session row for a token, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE token=?",
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def delete_auth_session(self, token: str) -> None:
        """Delete the session with this token, if any."""
        with self._conn() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))

    def create_live_execution_approval(  # noqa: PLR0913  # approval row columns; see exception-register.md
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
        """Insert a ``pending`` approval; see :meth:`StateDBBase.create_live_execution_approval`.

        Returns:
            The stored approval row, still ``pending``, or an empty dict when the row
            cannot be read back.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO live_execution_approvals (
                    id, requester_user_id, requester_username, scope_type, scope_id,
                    assignment_id, profile_id, status, reason_request, context_json,
                    requested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    approval_id, requester_user_id, requester_username,
                    scope_type, scope_id, assignment_id, profile_id,
                    "pending", reason_request, context_json, requested_at,
                ),
            )
        return self.get_live_execution_approval(approval_id) or {}

    def get_live_execution_approval(self, approval_id: str) -> dict | None:
        """Return the approval row with this id, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM live_execution_approvals WHERE id=?",
                (approval_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_pending_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> dict | None:
        """Return the newest ``pending`` approval for a scope, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_execution_approvals
                WHERE scope_type=? AND scope_id=? AND status='pending'
                ORDER BY requested_at DESC LIMIT 1
                """,
                (scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    def find_approved_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> dict | None:
        """Return the most recently decided ``approved`` approval for a scope, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_execution_approvals
                WHERE scope_type=? AND scope_id=? AND status='approved'
                ORDER BY decided_at DESC LIMIT 1
                """,
                (scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    def get_live_execution_approval_for_scope(
        self, scope_type: str, scope_id: str,
    ) -> dict | None:
        """Return the newest approval for a scope regardless of status, or ``None``."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_execution_approvals
                WHERE scope_type=? AND scope_id=?
                ORDER BY requested_at DESC LIMIT 1
                """,
                (scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    def list_live_execution_approvals(
        self, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        """Return the newest approvals, filtered by status unless it is ``None`` or ``"all"``."""
        with self._conn() as conn:
            if status and status != "all":
                rows = conn.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE status=?
                    ORDER BY requested_at DESC LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    ORDER BY requested_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def decide_live_execution_approval(
        self,
        approval_id: str,
        status: str,
        approver: PlatformUser,
        reason_decision: str,
        decided_at: str,
    ) -> dict | None:
        """Record a decision; see :meth:`StateDBBase.decide_live_execution_approval`.

        Returns:
            The approval row after the decision, returned unchanged when it was
            already decided, or ``None`` when the id is unknown.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM live_execution_approvals WHERE id=?",
                (approval_id,),
            ).fetchone()
            if not row:
                return None
            if row["status"] != "pending":
                return self.get_live_execution_approval(approval_id)
            conn.execute(
                """
                UPDATE live_execution_approvals
                SET status=?, approver_user_id=?, approver_username=?,
                    reason_decision=?, decided_at=?
                WHERE id=?
                """,
                (
                    status, approver.id, approver.username,
                    reason_decision, decided_at, approval_id,
                ),
            )
        return self.get_live_execution_approval(approval_id)
