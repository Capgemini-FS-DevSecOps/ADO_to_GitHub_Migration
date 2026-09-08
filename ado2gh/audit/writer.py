"""Append-only audit event writer.

Every payload passes through ``ado2gh.audit.redaction.redact_payload`` before
it is serialised, so no secret reaches the database (CA-003).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from ado2gh.audit.redaction import redact_payload

if TYPE_CHECKING:
    from ado2gh.state.base import StateDBBase


class AuditWriter:
    """Append-only audit events to StateDB."""

    def __init__(self, db: StateDBBase) -> None:
        """Bind the writer to the state database that stores audit events."""
        self.db = db

    def write(
        self,
        event_type: str,
        profile_id: str,
        actor: str = "",
        payload: Optional[dict] = None,
    ) -> str:
        """Persist one audit event after masking secrets in its payload.

        Args:
            event_type: Short machine name of what happened.
            profile_id: The profile the event belongs to.
            actor: Who caused it; empty when unknown.
            payload: Event details, redacted before they are serialised.

        Returns:
            The generated event id, prefixed ``aud_``.
        """
        event_id = f"aud_{uuid4().hex[:12]}"
        safe = redact_payload(payload or {})
        self.db.insert_audit_event(
            event_id=event_id,
            event_type=event_type,
            profile_id=profile_id,
            actor=actor,
            payload_json=json.dumps(safe),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return event_id
