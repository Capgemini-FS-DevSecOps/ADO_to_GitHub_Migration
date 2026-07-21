"""Deployment profile onboarding gate, lifecycle rules, and audit helpers."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from ado2gh.assignments.audit import AuditWriter
from ado2gh.auth.models import PlatformRole
from ado2gh.state.factory import create_state_db


class ProfileStatus(str, Enum):
    ACTIVE = "active"
    PENDING_APPROVAL = "pending_approval"
    DENIED = "denied"
    INACTIVE = "inactive"


class ProfileGovernanceError(Exception):
    """Profile invariant or lifecycle violation."""

    def __init__(self, message: str, code: str = "profile_governance_error"):
        super().__init__(message)
        self.code = code


def count_active_profiles(profiles: list[Any]) -> int:
    """Count profiles with status active."""
    return sum(1 for p in profiles if getattr(p, "status", ProfileStatus.ACTIVE.value) == ProfileStatus.ACTIVE.value)


def needs_profile_setup(profiles: list[Any]) -> bool:
    """True when no active deployment profile exists."""
    return count_active_profiles(profiles) == 0


def get_default_profile(profiles: list[Any]) -> Optional[Any]:
    """Return the profile marked default among active profiles, else first active."""
    active = [p for p in profiles if getattr(p, "status", ProfileStatus.ACTIVE.value) == ProfileStatus.ACTIVE.value]
    if not active:
        return None
    for p in active:
        if getattr(p, "is_default", False):
            return p
    return active[0]


def assert_profile_active_for_run(profile: Any) -> None:
    """Raise if profile cannot be used for migrations, discovery, or agent sessions."""
    status = getattr(profile, "status", ProfileStatus.ACTIVE.value)
    if status != ProfileStatus.ACTIVE.value:
        raise ProfileGovernanceError(
            f"Profile is not active (status={status})",
            code="profile_not_active",
        )


def assert_can_delete(
    profiles: list[Any],
    profile_id: str,
    new_default_profile_id: Optional[str] = None,
) -> None:
    """Validate delete/deactivate against minimum-one-active and default rules."""
    target = next((p for p in profiles if p.id == profile_id), None)
    if not target:
        raise ProfileGovernanceError("Profile not found", code="not_found")

    active = [p for p in profiles if getattr(p, "status", ProfileStatus.ACTIVE.value) == ProfileStatus.ACTIVE.value]
    if getattr(target, "status", ProfileStatus.ACTIVE.value) == ProfileStatus.ACTIVE.value and len(active) <= 1:
        raise ProfileGovernanceError(
            "Cannot remove the only active deployment profile",
            code="last_active_profile",
        )

    if getattr(target, "is_default", False) and len(active) > 1:
        if not new_default_profile_id:
            raise ProfileGovernanceError(
                "new_default_profile_id required when deleting the default profile",
                code="default_replacement_required",
            )
        replacement = next((p for p in active if p.id == new_default_profile_id), None)
        if not replacement or replacement.id == profile_id:
            raise ProfileGovernanceError(
                "new_default_profile_id must be another active profile",
                code="invalid_default_replacement",
            )
        if getattr(replacement, "status", ProfileStatus.ACTIVE.value) != ProfileStatus.ACTIVE.value:
            raise ProfileGovernanceError(
                "Replacement default profile must be active",
                code="invalid_default_replacement",
            )


def assert_operator_can_submit(profiles: list[Any], role: PlatformRole) -> None:
    """Operators may submit profiles only when at least one active profile exists."""
    if role == PlatformRole.ADMIN:
        return
    if role == PlatformRole.OPERATOR and needs_profile_setup(profiles):
        raise ProfileGovernanceError(
            "An admin must create the first active profile before operators can submit profiles",
            code="operator_submit_blocked",
        )


def onboarding_status_payload(
    profiles: list[Any],
    role: PlatformRole,
) -> dict[str, Any]:
    """Build GET /v1/onboarding/status response fields."""
    active_count = count_active_profiles(profiles)
    default = get_default_profile(profiles)
    pending_count = sum(
        1 for p in profiles
        if getattr(p, "status", "") == ProfileStatus.PENDING_APPROVAL.value
    )
    needs = needs_profile_setup(profiles)
    payload: dict[str, Any] = {
        "needs_profile_setup": needs,
        "active_profile_count": active_count,
        "default_profile_id": default.id if default else None,
        "pending_approval_count": pending_count,
        "role": role.value,
        "blocked_message": None,
        "redirect_path": "/onboarding/profile" if needs and role == PlatformRole.ADMIN else None,
        "can_submit_profile": role == PlatformRole.ADMIN or (role == PlatformRole.OPERATOR and active_count > 0),
    }
    if needs and role == PlatformRole.OPERATOR:
        payload["blocked_message"] = (
            "No deployment profile is active. Contact an administrator or wait for profile setup."
        )
        payload["can_submit_profile"] = False
    return payload


def write_profile_audit(
    event_type: str,
    profile_id: str,
    actor: str,
    payload: Optional[dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> str:
    """Emit redacted profile lifecycle audit event."""
    import os

    path = db_path or os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db")
    db = create_state_db(path)
    writer = AuditWriter(db)
    return writer.write(event_type, profile_id=profile_id, actor=actor, payload=payload or {})
