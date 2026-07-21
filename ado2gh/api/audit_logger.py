"""Audit event logging infrastructure for migration operations (feature 008).

Provides structured audit trail logging for all migration state changes,
satisfying Constitution Principle V (Enterprise Safeguards — CA-004).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from ado2gh.api.models import MigrationAuditEvent, AuditEventType
from ado2gh.api.state_db import get_state_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLogger:
    """Logs structured audit events for migration operations.

    All state changes (scan, approve, reject, revoke, migrate, rollback)
    are recorded with previous/new state, user ID, and optional reason
    for destructive actions.
    """

    def __init__(self, db=None):
        self._db = db or get_state_db()

    @property
    def db(self):
        return self._db

    def log_event(
        self,
        operation_id: str,
        event_type: str,
        previous_state: Optional[dict[str, Any]] = None,
        new_state: Optional[dict[str, Any]] = None,
        user_id: str = "",
        reason: Optional[str] = None,
    ) -> MigrationAuditEvent:
        """Record a single audit event.

        Raises ValueError if event_type is destructive and no reason is provided.
        """
        event = MigrationAuditEvent(
            operation_id=operation_id,
            event_type=event_type,
            previous_state=previous_state or {},
            new_state=new_state or {},
            user_id=user_id,
            reason=reason,
        )
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO migration_audit_events
                   (id, operation_id, event_type, previous_state_json,
                    new_state_json, user_id, reason, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                event.to_row(),
            )
        return event

    def log_scan(self, operation_id: str, user_id: str = "", **kwargs) -> MigrationAuditEvent:
        """Convenience method for scan events."""
        return self.log_event(
            operation_id=operation_id,
            event_type=AuditEventType.SCAN.value,
            new_state=kwargs,
            user_id=user_id,
        )

    def log_migrate(
        self,
        operation_id: str,
        previous_status: str,
        new_status: str,
        user_id: str = "",
        **kwargs,
    ) -> MigrationAuditEvent:
        """Convenience method for migration state transitions."""
        return self.log_event(
            operation_id=operation_id,
            event_type=AuditEventType.MIGRATE.value,
            previous_state={"status": previous_status},
            new_state={"status": new_status, **kwargs},
            user_id=user_id,
        )

    def log_rollback(
        self,
        operation_id: str,
        previous_status: str,
        reason: str,
        user_id: str = "",
        **kwargs,
    ) -> MigrationAuditEvent:
        """Convenience method for rollback events (requires reason)."""
        return self.log_event(
            operation_id=operation_id,
            event_type=AuditEventType.ROLLBACK.value,
            previous_state={"status": previous_status},
            new_state={"status": "rolled_back", **kwargs},
            user_id=user_id,
            reason=reason,
        )

    def log_approve(
        self,
        operation_id: str,
        user_id: str = "",
        **kwargs,
    ) -> MigrationAuditEvent:
        """Convenience method for approval events."""
        return self.log_event(
            operation_id=operation_id,
            event_type=AuditEventType.APPROVE.value,
            new_state=kwargs,
            user_id=user_id,
        )

    def log_reject(
        self,
        operation_id: str,
        reason: str,
        user_id: str = "",
        **kwargs,
    ) -> MigrationAuditEvent:
        """Convenience method for rejection events (requires reason)."""
        return self.log_event(
            operation_id=operation_id,
            event_type=AuditEventType.REJECT.value,
            new_state=kwargs,
            user_id=user_id,
            reason=reason,
        )

    def get_events(self, operation_id: str) -> list[MigrationAuditEvent]:
        """Retrieve all audit events for a given operation, ordered by time."""
        with self._db._conn() as conn:
            rows = conn.execute(
                """SELECT id, operation_id, event_type, previous_state_json,
                          new_state_json, user_id, reason, timestamp
                   FROM migration_audit_events
                   WHERE operation_id = ?
                   ORDER BY timestamp ASC""",
                (operation_id,),
            ).fetchall()
        return [MigrationAuditEvent.from_row(row) for row in rows]

    def get_event_count(self, operation_id: str) -> int:
        """Count audit events for an operation."""
        with self._db._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM migration_audit_events WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0]
