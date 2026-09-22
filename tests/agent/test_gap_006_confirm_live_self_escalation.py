"""Regression check for register entry GAP-006 (GAP-AGT-01): live execution can be self-granted and then goes unguarded.

Two reproductions, both currently failing:

1. `POST /v1/sessions/{id}/confirm-live` is gated only by the routine "operate"
   permission. An OPERATOR or COORDINATOR — roles that explicitly do *not* hold
   `can_approve_live_execution` — can therefore flip their own session out of
   dry-run and into live, GitHub-writing mode with one POST, with no approver
   review and no approval record of how live mode was reached.

2. The executor resolves dry-run as `migration_plan["dry_run"]` first and writes
   that value back onto the shared session dict. A plan-level `dry_run: False`
   therefore clobbers the session-level flag, and every downstream guardrail —
   which keys off `session["dry_run"]` alone — is disarmed for the rest of the
   session. A plan flag by itself must never turn a dry-run session live.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from ado2gh.agents.migration_agent.guardrails import GuardrailAction, evaluate_guardrail
from ado2gh.agents.migration_agent.nodes.executor.node import executor_node
from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService, permissions_for
from ado2gh.state.factory import create_state_db
from services.agent.main import _sessions, app

_PASSWORD = "GapSixPass12345!"


@pytest.fixture
def authed_agent(tmp_path, monkeypatch):
    """Agent client plus a login helper for roles that hold `can_operate`."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    db_path = tmp_path / "gap_006_confirm_live.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.create_user("gap6_operator", _PASSWORD, PlatformRole.OPERATOR, "Gap6 Operator")
    svc.create_user("gap6_coordinator", _PASSWORD, PlatformRole.COORDINATOR, "Gap6 Coordinator")

    agent = TestClient(app)
    accel = TestClient(accel_app)

    def login_as(username: str) -> TestClient:
        resp = accel.post("/v1/auth/login", json={"username": username, "password": _PASSWORD})
        assert resp.status_code == 200
        agent.cookies.set("ado2gh_session", resp.cookies.get("ado2gh_session"))
        return agent

    yield login_as
    _sessions.clear()


def _seed_dry_run_session(session_id: str, username: str) -> dict:
    """Put an owned, dry-run session carrying a migration plan into agent memory."""
    session = {
        "session_id": session_id,
        "profile_id": "lightweight",
        "user_username": username,
        "status": "idle",
        "messages": [],
        "dry_run": True,
        "live_approval_status": None,
        "plan_approved": True,
        "migration_plan": {
            "dry_run": True,
            "plan_id": "plan-gap6",
            "repo_order": ["contoso/payments"],
            "work_items": [{"repo": "contoso/payments", "scopes": ["git"], "status": "ready"}],
        },
    }
    _sessions[session_id] = session
    return session


@pytest.mark.parametrize(
    ("role", "username"),
    [(PlatformRole.OPERATOR, "gap6_operator"), (PlatformRole.COORDINATOR, "gap6_coordinator")],
)
def test_operate_only_role_cannot_self_confirm_live(authed_agent, role, username):
    """A caller lacking approve-live rights must not be able to grant live mode itself."""
    perms = permissions_for(role)
    assert perms["can_operate"] is True
    assert perms["can_approve_live_execution"] is False, "premise: role must lack approve-live"

    client = authed_agent(username)
    session_id = f"gap6-{role.value}"
    session = _seed_dry_run_session(session_id, username)

    resp = client.post(f"/v1/sessions/{session_id}/confirm-live")

    stored = _sessions[session_id]
    plan = stored.get("migration_plan") or {}
    now_live = plan.get("dry_run") is False or stored.get("dry_run") is False
    approved = stored.get("live_approval_status") == "approved"
    escalated = now_live and not approved

    assert not escalated, (
        f"{role.value} without can_approve_live_execution moved session to live "
        f"via confirm-live (HTTP {resp.status_code}) with no approval on record"
    )
    assert session is stored


async def _run_executor_with_live_plan() -> dict:
    """Drive the real executor over a dry-run session whose plan claims live mode."""
    session = {
        "session_id": "",  # no id → skips repo locking; no plan work to execute
        "profile_id": "lightweight",
        "status": "idle",
        "messages": [],
        "dry_run": True,
        "live_approval_status": None,
        "plan_approved": True,
        "user_role": PlatformRole.OPERATOR.value,
        "permissions": permissions_for(PlatformRole.OPERATOR),
    }
    session["migration_plan"] = {
        "dry_run": False,  # plan claims live; the session was never approved for it
        "plan_id": "plan-gap6",
        "work_items": [],
    }
    await executor_node({
        "session": session,
        "migration_plan": session["migration_plan"],
        "accel_get": AsyncMock(),
        "accel_post": AsyncMock(),
        "session_token": "fake-session-token-not-a-real-credential",
        "iteration": 0,
    })
    return session


async def test_plan_dry_run_flag_does_not_clobber_session_dry_run():
    """Plan data alone must not take the session out of dry-run."""
    session = await _run_executor_with_live_plan()

    assert session["dry_run"] is True, (
        "executor wrote migration_plan['dry_run']=False onto the session; a plan flag "
        "alone must not clear the session-level dry-run gate"
    )


async def test_plan_dry_run_flag_does_not_disarm_tool_guardrail():
    """The per-tool-call guardrail must still block GitHub writes after the executor runs."""
    session = await _run_executor_with_live_plan()

    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {"method": "POST", "path": "/orgs/contoso/repos", "target_resource": "contoso/payments"},
        migration_plan=session["migration_plan"],
        plan_approved=True,
        session=session,
    )

    assert decision.action is GuardrailAction.BLOCK, (
        "GitHub write allowed on a session that was never approved for live execution: "
        f"guardrail returned {decision.action.value} ({decision.reason})"
    )
