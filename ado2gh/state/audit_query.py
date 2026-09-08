"""Audit-event query helpers shared by the SQLite and PostgreSQL backends."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class AuditEventFilters:
    """Optional criteria for searching ``audit_events``; ``None`` means no restriction.

    ``actor`` and ``search`` match case-insensitively as substrings;
    ``search`` looks in the event type, actor and payload. ``date_to`` given
    as a bare ``YYYY-MM-DD`` covers the whole day.
    """

    profile_id: str | None = None
    actor: str | None = None
    event_type: str | None = None
    search: str | None = None
    date_from: str | None = None
    date_to: str | None = None


def normalize_date_to(value: str | None) -> str | None:
    """Extend a bare ``YYYY-MM-DD`` upper bound to the end of that day (UTC)."""
    if not value:
        return None
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text}T23:59:59.999999+00:00"
    return text


def build_audit_filters(filters: AuditEventFilters) -> tuple[list[str], list[Any]]:
    """Translate filters into SQL ``WHERE`` fragments and their bound values.

    Fragments use ``?`` placeholders; the PostgreSQL backend rewrites them
    to ``%s``. With no criteria the single fragment ``1=1`` is returned.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if filters.profile_id:
        clauses.append("profile_id = ?")
        params.append(filters.profile_id)
    if filters.actor:
        clauses.append("LOWER(actor) LIKE ?")
        params.append(f"%{filters.actor.strip().lower()}%")
    if filters.event_type:
        clauses.append("event_type = ?")
        params.append(filters.event_type.strip())
    if filters.search:
        needle = f"%{filters.search.strip().lower()}%"
        clauses.append(
            "(LOWER(event_type) LIKE ? OR LOWER(actor) LIKE ? OR LOWER(payload_json) LIKE ?)"
        )
        params.extend([needle, needle, needle])
    if filters.date_from:
        clauses.append("created_at >= ?")
        params.append(filters.date_from.strip())
    date_to = normalize_date_to(filters.date_to)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)

    if not clauses:
        return ["1=1"], []
    return clauses, params


def audit_events_to_csv(events: list[dict]) -> str:
    """Serialise audit rows to CSV text with the payload as one JSON column."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["id", "created_at", "event_type", "actor", "profile_id", "assignment_id", "payload"],
    )
    for row in events:
        payload = row.get("payload_json") or "{}"
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        writer.writerow([
            row.get("id", ""),
            row.get("created_at", ""),
            row.get("event_type", ""),
            row.get("actor", ""),
            row.get("profile_id", ""),
            row.get("assignment_id", "") or "",
            payload,
        ])
    return output.getvalue()
