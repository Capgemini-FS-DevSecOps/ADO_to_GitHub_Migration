"""The approve route turned a session live behind an identity gate that auth-off makes inert.

``services/agent/routes/execution_routes.py:160`` set ``session["dry_run"] = False`` and
``session["approval"] = {"approved": True, ...}`` with no platform approval row behind it.
The only gate in front was ``_require_approve_live`` (``:122``), and that helper is a
complete no-op whenever ``auth_enabled()`` is false — the configuration CLAUDE.md
documents as the default. An earlier fix removed that same auth-off dependency from the
policy functions in ``policies.py`` (register item GAP-002), but this route never reached
the policy layer: it wrote the flag itself, which bypasses the per-tool safeguard that
every confirmation or approval step must be kept and must cover only the exact action it
was granted for (register item CA-001), enforced in ``guardrails.py``.

The fix routes the transition through ``enforce_live_mode_request`` — the single check
every non-dry-run entry point uses — so the live decision is made once, in one place
(register item THR-06-004).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.agent.main import _sessions, app

ADMIN_PASSWORD = "AdminPass12345!"
USER_PASSWORD = "UserPass12345!"


@pytest.fixture
def anon_client(tmp_path, monkeypatch):
    """Agent service reached with no cookie, in the shipped default auth configuration."""
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "thr06004.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    yield TestClient(app)
    _sessions.clear()


@pytest.fixture
def authed(tmp_path, monkeypatch):
    """Agent client with real platform identities, for the authorised counterpart."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    db_path = tmp_path / "thr06004_auth.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", ADMIN_PASSWORD, "Admin")
    svc.create_user("operator1", USER_PASSWORD, PlatformRole.OPERATOR, "Operator One")

    agent = TestClient(app)
    accel = TestClient(accel_app)

    def cookie_for(username: str, password: str) -> str:
        login = accel.post(
            "/v1/auth/login", json={"username": username, "password": password},
        )
        assert login.status_code == 200, login.text
        return login.cookies.get("ado2gh_session")

    yield agent, {
        "admin": cookie_for("admin", ADMIN_PASSWORD),
        "op1": cookie_for("operator1", USER_PASSWORD),
    }
    _sessions.clear()


def test_anonymous_caller_cannot_approve_a_session_into_live_execution(anon_client):
    created = anon_client.post(
        "/v1/sessions", json={"profile_id": "lightweight", "dry_run": True},
    )
    sid = created.json()["session_id"]
    anon_client.post(f"/v1/sessions/{sid}/request-live")

    approved = anon_client.post(
        f"/v1/sessions/{sid}/approve", json={"approved": True, "reason": "ship it"},
    )

    assert approved.status_code in (401, 403), (
        f"a caller with no credentials approved a session into live execution "
        f"(HTTP {approved.status_code}); the gate is inert when ADO2GH_AUTH_ENABLED "
        f"is unset"
    )
    assert _sessions[sid].get("dry_run") is True, (
        "the session was left in live mode after an unauthenticated approval"
    )
    assert (_sessions[sid].get("approval") or {}).get("approved") is not True


def test_denial_still_works_without_credentials(anon_client):
    """Denying is the safe direction and must not be gated into unusability."""
    created = anon_client.post(
        "/v1/sessions", json={"profile_id": "lightweight", "dry_run": True},
    )
    sid = created.json()["session_id"]

    denied = anon_client.post(
        f"/v1/sessions/{sid}/approve", json={"approved": False, "reason": "not yet"},
    )

    assert denied.status_code == 200, denied.text
    assert _sessions[sid].get("dry_run") is True


def test_operator_without_approve_permission_cannot_approve(authed):
    agent, cookies = authed
    agent.cookies.set("ado2gh_session", cookies["op1"])
    created = agent.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]

    approved = agent.post(
        f"/v1/sessions/{sid}/approve", json={"approved": True, "reason": "ship it"},
    )

    assert approved.status_code == 403
    assert _sessions[sid].get("dry_run") is True


def test_admin_can_still_approve_a_session_into_live_execution(authed):
    agent, cookies = authed
    agent.cookies.set("ado2gh_session", cookies["admin"])
    created = agent.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]

    approved = agent.post(
        f"/v1/sessions/{sid}/approve", json={"approved": True, "reason": "reviewed"},
    )

    assert approved.status_code == 200, approved.text
    assert _sessions[sid]["dry_run"] is False
