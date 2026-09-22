"""Role-aware audit history visibility."""
from __future__ import annotations

from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import auth_enabled


def can_view_all_audit_history(user: PlatformUser | None) -> bool:
    """Report whether a user may read every actor's audit events.

    Args:
        user: The authenticated platform user, or None when the request carried
            no session.

    Returns:
        True when the caller may read the whole platform audit trail — that is,
        when platform auth is disabled altogether or the user holds the admin
        role. False for operators and for unauthenticated requests, which are
        scoped to their own events by ``resolve_audit_actor_filter``.
    """
    # Scoped role-based access control (RBAC) exclusion, deliberate. With platform auth disabled there is no
    # role to read, so single-operator local and lightweight deployments would
    # otherwise be scoped to an actor that does not exist and see an empty audit
    # history. This is not a missing authorization check: it follows the identity
    # gate convention documented in ado2gh/api/platform_rbac.py, and the routes
    # that call this are guarded by require_operate, which is permissive in
    # exactly the same auth-disabled mode. Do not "harden" this to return False.
    if not auth_enabled():
        return True
    if not user:
        return False
    role = getattr(user, "role", None)
    return role == PlatformRole.ADMIN


def resolve_audit_actor_filter(user: PlatformUser | None, requested_actor: str | None) -> str | None:
    """Resolve the actor an audit history query is allowed to filter on.

    Admins and callers in auth-disabled deployments get the actor they asked
    for. Operators are forced onto their own username regardless of what they
    requested, so an operator cannot read another user's events by passing a
    different actor.

    Args:
        user: The authenticated platform user, or None when the request carried
            no session.
        requested_actor: The actor supplied on the query string, if any.

    Returns:
        The requested actor for callers who may view the whole trail, the
        operator's own username for callers who may not, or None to mean no
        actor filter when nothing was requested or the user has no username.
    """
    if not auth_enabled():
        return requested_actor or None
    if not user:
        return requested_actor or None
    if can_view_all_audit_history(user):
        return requested_actor or None
    return getattr(user, "username", None)
