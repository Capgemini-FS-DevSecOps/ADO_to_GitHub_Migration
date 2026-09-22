"""Platform role-based access control (RBAC) guards for accelerator and agent routes.

Every guard here except the two live-execution ones is an *identity* gate: it stays
permissive when ``ADO2GH_AUTH_ENABLED`` is unset so that local development, which the
project documents as running with auth off, keeps working.

``require_approve_live_execution`` and ``operator_requires_live_approval`` are *safety*
gates, not identity gates. They stay armed whatever ``ADO2GH_AUTH_ENABLED`` says,
because turning identity checking off must not also turn off the human approval an
irreversible write to GitHub requires (GAP-002, Principle V).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from ado2gh.auth.models import PlatformUser
from ado2gh.auth.service import auth_enabled, permissions_for
from ado2gh.models import ExecutionMode

logger = logging.getLogger(__name__)

LIVE_APPROVAL_REFUSAL_EVENT = "platform.live_execution.approval_refused"
"""Audit event name written by `require_approve_live_execution` on every refusal (GAP-134).

A safeguard that every real action is audited only holds if every refusal is
audited too — otherwise a caller repeatedly denied live-execution approval
leaves no trace outside the request logs.
"""


def _audit_live_approval_refusal(
    request: Request, user: PlatformUser | None, reason: str,
) -> None:
    """Write a masked audit record for a refused live-execution approval (GAP-134).

    Never blocks the refusal it is recording: a broken audit backend still
    lets `require_approve_live_execution` raise its 401 or 403, and the audit
    failure itself is only logged, at warning level, with no request content.

    Args:
        request: The incoming request being refused.
        user: The identity on the request, or ``None`` when it carries none.
        reason: ``"no_identity"`` for a request with no signed-in user,
            ``"missing_capability"`` for one whose role cannot approve live
            execution.
    """
    from ado2gh.api.profile_governance import write_profile_audit
    from ado2gh.api.settings_store import SettingsStore

    try:
        active = SettingsStore().get_active_profile()
    except Exception:
        active = None
    profile_id = (active.id if active else None) or "_platform"

    try:
        write_profile_audit(
            LIVE_APPROVAL_REFUSAL_EVENT,
            profile_id=profile_id,
            actor=getattr(user, "username", "") or "",
            payload={
                "reason": reason,
                "capability": "can_approve_live_execution",
                "path": request.url.path,
            },
        )
    except Exception:
        logger.warning(
            "Failed to record audit event for a refused live-execution approval "
            "(reason=%s)", reason,
        )


def platform_user(request: Request) -> PlatformUser | None:
    """Read the identity the authentication middleware attached to a request.

    Args:
        request: The incoming request.

    Returns:
        The signed-in ``PlatformUser``, or ``None`` when the request carries no
        identity at all — either authentication is disabled, or no session was
        presented.
    """
    return getattr(request.state, "platform_user", None)


def _require_authenticated(request: Request) -> PlatformUser | None:
    """Reject a request that carries no identity while authentication is on.

    Args:
        request: The incoming request.

    Returns:
        The signed-in user, or ``None`` when ``ADO2GH_AUTH_ENABLED`` is unset and
        the request is therefore allowed through anonymously.

    Raises:
        HTTPException: 401 when authentication is enabled and no identity is present.
    """
    user = platform_user(request)
    if not auth_enabled():
        return user
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_capability(request: Request, capability: str) -> PlatformUser | None:
    """Require that the caller's role grants a named capability.

    Args:
        request: The incoming request.
        capability: Capability key as returned by ``permissions_for``, for example
            ``can_operate`` or ``can_manage_settings``.

    Returns:
        The signed-in user, or ``None`` when authentication is disabled — the check
        is deliberately permissive then, so local development keeps working.

    Raises:
        HTTPException: 401 when authentication is enabled and the request carries no
            identity, 403 when the caller's role does not grant the capability.
    """
    user = _require_authenticated(request)
    if not auth_enabled():
        return user
    # auth_enabled() is True here, and _require_authenticated's own body
    # proves it never returns None in that case (it raises 401 instead).
    assert user is not None
    perms = permissions_for(user.role)
    if not perms.get(capability):
        raise HTTPException(status_code=403, detail=f"Missing capability: {capability}")
    return user


def require_manage_settings(request: Request) -> PlatformUser | None:
    """Require the ``can_manage_settings`` capability for a settings write.

    Args:
        request: The incoming request.

    Returns:
        The signed-in user, or ``None`` when authentication is disabled.

    Raises:
        HTTPException: 401 without an identity, 403 without the capability.
    """
    return require_capability(request, "can_manage_settings")


def require_manage_models(request: Request) -> PlatformUser | None:
    """Require the ``can_manage_models`` capability for a language-model change.

    Args:
        request: The incoming request.

    Returns:
        The signed-in user, or ``None`` when authentication is disabled.

    Raises:
        HTTPException: 401 without an identity, 403 without the capability.
    """
    return require_capability(request, "can_manage_models")


def require_approve_live_execution(request: Request) -> PlatformUser | None:
    """Grant or deny a live run. Armed even with ``ADO2GH_AUTH_ENABLED`` off.

    Deliberately does not go through ``require_capability``: that helper returns the
    (possibly ``None``) user unchecked when auth is disabled, which made this guard a
    no-op in the shipped default configuration.

    Args:
        request: The incoming request.

    Returns:
        The signed-in user holding ``can_approve_live_execution``. Never ``None``:
        an anonymous request is refused rather than waved through.

    Raises:
        HTTPException: 401 when the request carries no identity, 403 when the
            caller's role cannot approve live execution.
    """
    user = platform_user(request)
    if not user:
        logger.warning("Refused live-execution approval: request carries no identity")
        _audit_live_approval_refusal(request, user, "no_identity")
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not permissions_for(user.role).get("can_approve_live_execution"):
        _audit_live_approval_refusal(request, user, "missing_capability")
        raise HTTPException(
            status_code=403, detail="Missing capability: can_approve_live_execution",
        )
    return user


def require_operate(request: Request) -> PlatformUser | None:
    """Require the ``can_operate`` capability for a migration action.

    Args:
        request: The incoming request.

    Returns:
        The signed-in user, or ``None`` when authentication is disabled.

    Raises:
        HTTPException: 401 without an identity, 403 without the capability.
    """
    return require_capability(request, "can_operate")


def operator_requires_live_approval(
    user: PlatformUser | None, mode: ExecutionMode,
) -> bool:
    """Must this caller be routed through ``LiveApprovalStore`` before executing live?

    Capability-derived rather than role-name-derived, so every role that can operate
    but cannot approve — COORDINATOR as well as OPERATOR — is queued (GAP-005).

    A live request carrying no identity at all is refused outright rather than queued:
    there is no actor to attribute it to and no ``PlatformUser`` to open an approval on
    behalf of, so "requires approval" would be unsatisfiable. Dry-run is untouched, so
    auth-disabled local development keeps working for everything reversible.

    Args:
        user: The identity on the request, or ``None`` when it carries none.
        mode: Mode the run would execute in. ``ExecutionMode.DRY_RUN`` never needs
            approval. Convert an incoming ``dry_run`` boolean — HTTP body, stored run
            record — with ``ExecutionMode.from_dry_run`` at the boundary (CA-001).

    Returns:
        ``True`` when the run must be parked in the approval queue because the caller
        may operate but may not approve live execution; ``False`` when it may proceed
        immediately, either because it is a dry run or because the caller can approve.

    Raises:
        HTTPException: 401 when a live run carries no identity.
    """
    if mode is ExecutionMode.DRY_RUN:
        return False
    if not user:
        logger.warning("Refused live execution: request carries no identity")
        raise HTTPException(status_code=401, detail="Not authenticated")
    return not permissions_for(user.role).get("can_approve_live_execution", False)
