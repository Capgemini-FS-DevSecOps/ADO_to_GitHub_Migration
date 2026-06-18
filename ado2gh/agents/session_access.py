"""Agent session visibility scoped by platform user and deployment profile."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import auth_enabled


def request_username(request: Request | None) -> str | None:
    if not request:
        return None
    user = getattr(request.state, "platform_user", None)
    return getattr(user, "username", None) if user else None


def is_admin_request(request: Request | None) -> bool:
    if not request:
        return not auth_enabled()
    user = getattr(request.state, "platform_user", None)
    if not user:
        return not auth_enabled()
    return getattr(user, "role", None) == PlatformRole.ADMIN


def session_owner_username(session: dict[str, Any]) -> str | None:
    return session.get("user_username")


def can_access_agent_session(
    request: Request | None,
    session: dict[str, Any],
    *,
    profile_id: str | None = None,
    write: bool = False,
) -> bool:
    if profile_id and session.get("profile_id") != profile_id:
        return False
    if not auth_enabled():
        return True
    owner = session_owner_username(session)
    viewer = request_username(request)
    if not owner or not viewer:
        return True
    if is_admin_request(request):
        return True
    if owner == viewer:
        return True
    if write:
        return False
    perms = getattr(request.state, "permissions", None) or {}
    return bool(perms.get("can_approve_live_execution"))


def assert_agent_session_access(
    request: Request | None,
    session: dict[str, Any],
    *,
    profile_id: str | None = None,
    write: bool = False,
) -> None:
    if not can_access_agent_session(request, session, profile_id=profile_id, write=write):
        raise HTTPException(status_code=404, detail="session_not_found")
