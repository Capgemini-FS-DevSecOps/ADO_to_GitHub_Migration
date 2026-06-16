"""Immutable audit event writer with secret redaction (CA-003)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

_SECRET_PATTERNS = [
    re.compile(r"(ghp_[A-Za-z0-9_]+)", re.I),
    re.compile(r"(gho_[A-Za-z0-9_]+)", re.I),
    re.compile(r"(pat-[A-Za-z0-9]+)", re.I),
    re.compile(r'("(?:token|password|secret|pat)"\s*:\s*)"[^"]*"', re.I),
]


def redact_payload(payload: Any) -> Any:
    """Recursively redact likely secrets from audit payloads."""
    if payload is None:
        return None
    if isinstance(payload, str):
        out = payload
        for pat in _SECRET_PATTERNS:
            out = pat.sub(lambda m: m.group(0)[:4] + "***", out)
        return out
    if isinstance(payload, dict):
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(x) for x in payload]
    return payload


class AuditWriter:
    """Append-only audit events to StateDB."""

    def __init__(self, db: Any):
        self.db = db

    def write(
        self,
        event_type: str,
        profile_id: str,
        actor: str = "",
        assignment_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> str:
        """Record an audit event; returns event id."""
        event_id = f"aud_{uuid4().hex[:12]}"
        safe = redact_payload(payload or {})
        self.db.insert_audit_event(
            event_id=event_id,
            event_type=event_type,
            profile_id=profile_id,
            actor=actor,
            assignment_id=assignment_id,
            payload_json=json.dumps(safe),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return event_id
