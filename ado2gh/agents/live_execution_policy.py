"""Agent-side live migration execution policy (mirrors platform RBAC)."""
from __future__ import annotations

from typing import Any

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import auth_enabled, permissions_for


def attach_actor_to_session(session: dict[str, Any], user: Any | None) -> None:
    """Persist platform user identity on an agent session."""
    if not user:
        return
    session["user_id"] = getattr(user, "id", None)
    session["user_username"] = getattr(user, "username", None)
    session["user_role"] = getattr(user.role, "value", str(getattr(user, "role", "")))
    session["user_display_name"] = getattr(user, "display_name", None) or getattr(
        user, "username", None,
    )
    session["permissions"] = permissions_for(user.role)


def _role_may_execute_live(role: str | None) -> bool:
    if not role:
        return False
    return role in (
        PlatformRole.ADMIN.value,
        PlatformRole.APPROVER.value,
    )


def can_execute_live_without_approval(session: dict[str, Any]) -> bool:
    """True when this session may start a live migration without the approval queue."""
    if session.get("dry_run", True):
        return True
    if not auth_enabled():
        return True
    if session.get("live_approval_status") == "approved":
        return True
    perms = session.get("permissions") or {}
    if perms.get("can_approve_live_execution"):
        return True
    return _role_may_execute_live(session.get("user_role"))


def session_requires_live_approval(session: dict[str, Any]) -> bool:
    """True when a live run must not start until platform approval."""
    if session.get("dry_run", True):
        return False
    if not auth_enabled():
        return False
    if session.get("live_approval_status") == "approved":
        return False
    if can_execute_live_without_approval(session):
        return False
    return session.get("user_role") == PlatformRole.OPERATOR.value or not session.get("user_role")


def live_execution_block_message(session: dict[str, Any]) -> str:
    name = session.get("user_display_name") or session.get("user_username") or "operator"
    role = session.get("user_role") or "unknown"
    profile = session.get("profile_id") or "active profile"
    return (
        "### Live execution requires approval\n\n"
        f"You are signed in as **{name}** (`{role}`) using deployment profile "
        f"**{profile}**.\n\n"
        "A **live** migration writes to GitHub and **cannot start** until a platform "
        "**admin** or **approver** reviews and approves this run.\n\n"
        "- **Dry-run** migrations are safe to execute without approval.\n"
        "- Use **Request live execution** or ask an approver to act from "
        "**Settings → Approvals**.\n\n"
        "**No migration was started.**"
    )


def execution_policy_summary(session: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(session.get("dry_run", True))
    requires = session_requires_live_approval(session)
    perms = session.get("permissions") or {}
    return {
        "dry_run": dry_run,
        "requires_live_approval": requires,
        "live_approved": session.get("live_approval_status") == "approved",
        "can_execute_live_without_approval": can_execute_live_without_approval(session),
        "can_approve_live_execution": bool(perms.get("can_approve_live_execution")),
        "user_role": session.get("user_role"),
        "user_display_name": session.get("user_display_name"),
    }
