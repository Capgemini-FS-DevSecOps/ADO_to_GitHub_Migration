"""Audit bridge for local IDE sessions and MCP tool calls."""
from __future__ import annotations

from typing import Any, Optional

from ado2gh.assignments.audit import AuditWriter
from ado2gh.state.factory import create_state_db


def _state_db():
    return create_state_db()


class IdeAuditBridge:
    """Writes audit_events for IDE session and tool actions."""

    def __init__(self, db: Optional[Any] = None):
        self._db = db or _state_db()
        self._writer = AuditWriter(self._db)

    def record(
        self,
        action: str,
        profile_id: str,
        actor: str = "local-developer",
        assignment_id: Optional[str] = None,
        session_id: Optional[str] = None,
        outcome: str = "success",
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Append audit event; returns event id.

        session_id and outcome stored in redacted payload metadata.
        """
        payload = dict(metadata or {})
        payload["outcome"] = outcome
        if session_id:
            payload["session_id"] = session_id
        event_type = f"ide.{action}" if not action.startswith("ide.") else action
        if action.startswith("tool."):
            event_type = action
        return self._writer.write(
            event_type=event_type,
            profile_id=profile_id,
            actor=actor,
            assignment_id=assignment_id,
            payload=payload,
        )
