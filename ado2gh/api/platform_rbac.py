"""Platform RBAC guards for accelerator and agent routes.

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

logger = logging.getLogger(__name__)


def platform_user(request: Request) -> PlatformUser | None:
    return getattr(request.state, "platform_user", None)


def _require_authenticated(request: Request) -> PlatformUser | None:
    user = platform_user(request)
    if not auth_enabled():
        return user
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_capability(request: Request, capability: str) -> PlatformUser | None:
    user = _require_authenticated(request)
    if not auth_enabled():
        return user
    perms = permissions_for(user.role)
    if not perms.get(capability):
        raise HTTPException(status_code=403, detail=f"Missing capability: {capability}")
    return user


def require_manage_settings(request: Request) -> PlatformUser | None:
    return require_capability(request, "can_manage_settings")


def require_manage_models(request: Request) -> PlatformUser | None:
    return require_capability(request, "can_manage_models")


def require_approve_live_execution(request: Request) -> PlatformUser | None:
    """Grant or deny a live run. Armed even with ``ADO2GH_AUTH_ENABLED`` off.

    Deliberately does not go through ``require_capability``: that helper returns the
    (possibly ``None``) user unchecked when auth is disabled, which made this guard a
    no-op in the shipped default configuration.
    """
    user = platform_user(request)
    if not user:
        logger.warning("Refused live-execution approval: request carries no identity")
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not permissions_for(user.role).get("can_approve_live_execution"):
        raise HTTPException(
            status_code=403, detail="Missing capability: can_approve_live_execution",
        )
    return user


def require_operate(request: Request) -> PlatformUser | None:
    return require_capability(request, "can_operate")


def operator_requires_live_approval(user: PlatformUser | None, dry_run: bool) -> bool:
    """Must this caller be routed through ``LiveApprovalStore`` before executing live?

    Capability-derived rather than role-name-derived, so every role that can operate
    but cannot approve — COORDINATOR as well as OPERATOR — is queued (GAP-005).

    A live request carrying no identity at all is refused outright rather than queued:
    there is no actor to attribute it to and no ``PlatformUser`` to open an approval on
    behalf of, so "requires approval" would be unsatisfiable. Dry-run is untouched, so
    auth-disabled local development keeps working for everything reversible.
    """
    if dry_run:
        return False
    if not user:
        logger.warning("Refused live execution: request carries no identity")
        raise HTTPException(status_code=401, detail="Not authenticated")
    return not permissions_for(user.role).get("can_approve_live_execution", False)
