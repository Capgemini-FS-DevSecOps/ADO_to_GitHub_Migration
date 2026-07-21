"""Platform RBAC guards for accelerator and agent routes."""
from __future__ import annotations

from fastapi import HTTPException, Request

from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import auth_enabled, permissions_for


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
    return require_capability(request, "can_approve_live_execution")


def require_operate(request: Request) -> PlatformUser | None:
    return require_capability(request, "can_operate")


def operator_requires_live_approval(user: PlatformUser | None, dry_run: bool) -> bool:
    if dry_run or not auth_enabled() or not user:
        return False
    return user.role == PlatformRole.OPERATOR
