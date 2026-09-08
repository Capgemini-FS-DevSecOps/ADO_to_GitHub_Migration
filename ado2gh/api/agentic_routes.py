"""REST routes for the audit history API."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Request, Response

from ado2gh.api.audit_access import can_view_all_audit_history, resolve_audit_actor_filter
from ado2gh.api.platform_rbac import require_operate
from ado2gh.state.audit_query import AuditEventFilters, audit_events_to_csv
from ado2gh.state.factory import create_state_db

router = APIRouter(tags=["agentic"])


def _db_path() -> str:
    return os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db")


def _audit_search_filters(
    profile_id: Optional[str] = None,
    actor: Optional[str] = None,
    event_type: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> AuditEventFilters:
    return AuditEventFilters(
        profile_id=profile_id or None,
        actor=actor or None,
        event_type=event_type or None,
        search=search or None,
        date_from=date_from or None,
        date_to=date_to or None,
    )


@router.get("/v1/history/sessions")
def history_sessions(
    request: Request,
    profile_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    actor: Optional[str] = None,
    event_type: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """FR-039 — paginated audit trail with optional filters."""
    require_operate(request)
    user = getattr(request.state, "platform_user", None)
    scoped_actor = resolve_audit_actor_filter(user, actor)
    db = create_state_db(_db_path())
    filters = _audit_search_filters(
        profile_id, scoped_actor, event_type, search, date_from, date_to,
    )
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
def history_event_types(request: Request, profile_id: Optional[str] = None, limit: int = 200):
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
    profile_id: Optional[str] = None,
    actor: Optional[str] = None,
    event_type: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 10000,
):
    """Download filtered audit events as CSV."""
    require_operate(request)
    user = getattr(request.state, "platform_user", None)
    scoped_actor = resolve_audit_actor_filter(user, actor)
    db = create_state_db(_db_path())
    filters = _audit_search_filters(
        profile_id, scoped_actor, event_type, search, date_from, date_to,
    )
    events = db.search_audit_events(filters, limit=min(limit, 10000), offset=0)
    csv_body = audit_events_to_csv(events)
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-history.csv"',
        },
    )
