"""Role-aware audit history visibility."""
from __future__ import annotations

from typing import Any

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import auth_enabled


def can_view_all_audit_history(user: Any | None) -> bool:
    """Admins see the full platform audit trail; operators see only their own events."""
    if not auth_enabled():
        return True
    if not user:
        return False
    role = getattr(user, "role", None)
    return role == PlatformRole.ADMIN


def resolve_audit_actor_filter(user: Any | None, requested_actor: str | None) -> str | None:
    """Operators are always scoped to their username; admins may filter by actor."""
    if not auth_enabled():
        return requested_actor or None
    if not user:
        return requested_actor or None
    if can_view_all_audit_history(user):
        return requested_actor or None
    return getattr(user, "username", None)
