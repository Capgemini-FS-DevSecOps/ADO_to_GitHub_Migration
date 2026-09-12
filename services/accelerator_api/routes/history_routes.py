"""REST routes for the audit history API."""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, Response

from ado2gh.api.audit_access import can_view_all_audit_history, resolve_audit_actor_filter
from ado2gh.api.platform_rbac import require_operate
from ado2gh.state.audit_query import AuditEventFilters, audit_events_to_csv
from ado2gh.state.factory import create_state_db

router = APIRouter(tags=["agentic"])


def _db_path() -> str:
    """Locate the state database holding the audit events.

    Returns:
        The path from ``ADO2GH_SQLITE_PATH``, or ``migration_state.db`` in the
        working directory when that variable is unset. Read per call so a process
        that changes it mid-run is honoured.
    """
    return os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db")


@router.get("/v1/history/sessions")
def history_sessions(
    request: Request,
    filters: AuditEventFilters = Depends(),
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List recorded audit events, newest first (FR-039).

    Requires the ``can_operate`` capability. A caller who may not view the whole
    organisation's history is silently restricted to their own username, whatever
    ``actor`` they ask for; the response says so in ``scoped_to_actor``.

    Args:
        request: The incoming request, carrying the caller's identity.
        filters: Query parameters ``profile_id``, ``actor``, ``event_type``,
            ``search``, ``date_from`` and ``date_to``. Each is optional and omitting
            one leaves that dimension unfiltered. ``actor`` and ``search`` match as
            case-insensitive substrings, ``search`` across event type, actor and
            payload; a ``date_to`` given as ``YYYY-MM-DD`` covers the whole day.
        limit: Maximum number of events to return in this page.
        offset: Number of matching events to skip before the page starts.

    Returns:
        A JSON object with ``sessions`` — the page of audit events, each with its
        id, timestamp, event type, actor, profile and payload — plus ``total``
        matching events before paging, the echoed ``limit`` and ``offset``,
        ``count`` for the events actually returned, ``scoped_to_actor`` naming the
        actor the caller was confined to (``null`` when unconfined) and
        ``can_view_all``.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``.
    """
    require_operate(request)
    user = getattr(request.state, "platform_user", None)
    scoped_actor = resolve_audit_actor_filter(user, filters.actor)
    filters.actor = scoped_actor
    db = create_state_db(_db_path())
    events = db.search_audit_events(filters, limit=limit, offset=offset)
    total = db.count_audit_events(filters)
    return {
        "sessions": events,
        "total": total,
        "limit": limit,
        "offset": offset,
        "count": len(events),
        "scoped_to_actor": scoped_actor if not can_view_all_audit_history(user) else None,
        "can_view_all": can_view_all_audit_history(user),
    }


@router.get("/v1/history/event-types")
def history_event_types(
    request: Request, profile_id: Optional[str] = None, limit: int = 200,
) -> dict[str, Any]:
    """List the distinct audit event types available for filtering.

    Requires the ``can_operate`` capability. A caller who may not view the whole
    organisation's history sees only the event types their own activity produced,
    so the filter drop-down never reveals what other people did.

    Args:
        request: The incoming request, carrying the caller's identity.
        profile_id: Restrict the event types to one migration profile. Omit for all.
        limit: Maximum number of distinct event types to return.

    Returns:
        A JSON object with ``event_types``, the distinct event-type names, and
        ``can_view_all`` reporting whether the caller sees the whole organisation.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``.
    """
    require_operate(request)
    user = getattr(request.state, "platform_user", None)
    scoped_actor = resolve_audit_actor_filter(user, None)
    db = create_state_db(_db_path())
    return {
        "event_types": db.list_audit_event_types(
            profile_id=profile_id or None,
            limit=limit,
            actor=scoped_actor,
        ),
        "can_view_all": can_view_all_audit_history(user),
    }


@router.get("/v1/history/sessions/export")
def export_history_sessions(
    request: Request,
    filters: AuditEventFilters = Depends(),
    limit: int = 10000,
) -> Response:
    """Download the filtered audit events as a CSV attachment.

    Takes the same filters as ``GET /v1/history/sessions`` and applies the same
    per-caller actor scoping, but returns one file rather than a page. Requires the
    ``can_operate`` capability.

    Args:
        request: The incoming request, carrying the caller's identity.
        filters: Query parameters ``profile_id``, ``actor``, ``event_type``,
            ``search``, ``date_from`` and ``date_to``, interpreted exactly as on
            ``GET /v1/history/sessions``.
        limit: Maximum number of events to export. Capped at 10000 whatever is asked.

    Returns:
        A ``text/csv`` response, sent as the attachment ``audit-history.csv``, with
        one row per event: id, timestamp, event type, actor, profile, assignment and
        the payload as a single JSON column.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_operate``.
    """
    require_operate(request)
    user = getattr(request.state, "platform_user", None)
    filters.actor = resolve_audit_actor_filter(user, filters.actor)
    db = create_state_db(_db_path())
    events = db.search_audit_events(filters, limit=min(limit, 10000), offset=0)
    csv_body = audit_events_to_csv(events)
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-history.csv"',
        },
    )
