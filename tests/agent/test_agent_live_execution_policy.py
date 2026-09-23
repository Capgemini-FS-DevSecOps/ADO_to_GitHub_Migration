"""Live execution approval policy for agent sessions."""
import pytest

from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
    can_execute_live_without_approval,
    execution_policy_summary,
    live_execution_block_message,
    session_requires_live_approval,
)
from ado2gh.auth.models import PlatformRole, PlatformUser


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")


def test_operator_live_requires_approval():
    session = {"dry_run": False, "user_role": PlatformRole.OPERATOR.value}
    assert session_requires_live_approval(session) is True


def test_admin_live_does_not_require_approval():
    session = {"dry_run": False, "user_role": PlatformRole.ADMIN.value}
    assert session_requires_live_approval(session) is False
    assert can_execute_live_without_approval(session) is True


def test_approver_live_does_not_require_approval():
    session = {"dry_run": False, "user_role": PlatformRole.APPROVER.value}
    assert session_requires_live_approval(session) is False
    assert can_execute_live_without_approval(session) is True


def test_permissions_grant_live_without_role():
    session = {
        "dry_run": False,
        "permissions": {"can_approve_live_execution": True},
    }
    assert session_requires_live_approval(session) is False
    assert can_execute_live_without_approval(session) is True


def test_dry_run_never_requires_approval():
    session = {"dry_run": True, "user_role": PlatformRole.OPERATOR.value}
    assert session_requires_live_approval(session) is False


def test_approved_live_operator_allowed():
    session = {
        "dry_run": False,
        "user_role": PlatformRole.OPERATOR.value,
        "live_approval_status": "approved",
    }
    assert session_requires_live_approval(session) is False


def test_attach_actor_to_session():
    session: dict = {}
    user = PlatformUser("u1", "op", PlatformRole.OPERATOR, "Operator One")
    attach_actor_to_session(session, user)
    assert session["user_role"] == "operator"
    assert session["user_display_name"] == "Operator One"
    assert session["permissions"]["can_operate"] is True
    assert session["permissions"]["can_approve_live_execution"] is False


def test_block_message_mentions_role():
    msg = live_execution_block_message({
        "user_display_name": "Op User",
        "user_role": "operator",
        "profile_id": "prof-1",
    })
    assert "approval" in msg.lower()
    assert "Op User" in msg
    assert "prof-1" in msg


def test_execution_policy_summary():
    summary = execution_policy_summary({
        "dry_run": False,
        "user_role": "operator",
        "permissions": {"can_approve_live_execution": False},
    })
    assert summary["requires_live_approval"] is True
    assert summary["can_approve_live_execution"] is False
    assert summary["can_execute_live_without_approval"] is False
