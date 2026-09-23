"""Agent session access scoped by user and deployment profile."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.agent.main import _sessions, app


@pytest.fixture
def authed_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    db_path = tmp_path / "agent_access.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app as accel_app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    svc = auth_routes._svc
    svc.bootstrap_admin("admin", "AdminPass12345!", "Admin")
    svc.create_user("operator1", "OpPass12345!", PlatformRole.OPERATOR, "Operator One")
    svc.create_user("operator2", "OpPass12345!", PlatformRole.OPERATOR, "Operator Two")

    agent = TestClient(app)
    accel = TestClient(accel_app)

    def cookie_for(username: str, password: str) -> str:
        login = accel.post("/v1/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        return login.cookies.get("ado2gh_session")

    yield {
        "agent": agent,
        "cookies": {
            "op1": cookie_for("operator1", "OpPass12345!"),
            "op2": cookie_for("operator2", "OpPass12345!"),
            "admin": cookie_for("admin", "AdminPass12345!"),
        },
    }
    _sessions.clear()


def _create_chat(client: TestClient, cookie: str, profile_id: str, prompt: str) -> str:
    client.cookies.set("ado2gh_session", cookie)
    created = client.post(
        "/v1/sessions",
        json={"profile_id": profile_id, "prompt": prompt, "dry_run": True},
    )
    assert created.status_code == 200
    return created.json()["session_id"]


def test_operator_cannot_read_other_users_session(authed_agents):
    agent = authed_agents["agent"]
    sid = _create_chat(agent, authed_agents["cookies"]["op1"], "lightweight", "Hello from op1")

    agent.cookies.set("ado2gh_session", authed_agents["cookies"]["op2"])
    denied = agent.get(f"/v1/sessions/{sid}")
    assert denied.status_code == 404


def test_operator_lists_only_own_sessions(authed_agents):
    agent = authed_agents["agent"]
    sid1 = _create_chat(agent, authed_agents["cookies"]["op1"], "lightweight", "Op1 chat")
    sid2 = _create_chat(agent, authed_agents["cookies"]["op2"], "lightweight", "Op2 chat")

    agent.cookies.set("ado2gh_session", authed_agents["cookies"]["op1"])
    listed = agent.get("/v1/sessions?profile_id=lightweight")
    assert listed.status_code == 200
    ids = {item["session_id"] for item in listed.json()["sessions"]}
    assert sid1 in ids
    assert sid2 not in ids


def test_sessions_isolated_by_profile(authed_agents):
    agent = authed_agents["agent"]
    sid_a = _create_chat(agent, authed_agents["cookies"]["op1"], "lightweight", "Profile A chat")
    sid_b = _create_chat(agent, authed_agents["cookies"]["op1"], "enterprise", "Profile B chat")

    agent.cookies.set("ado2gh_session", authed_agents["cookies"]["op1"])
    listed_a = agent.get("/v1/sessions?profile_id=lightweight")
    ids_a = {item["session_id"] for item in listed_a.json()["sessions"]}
    assert sid_a in ids_a
    assert sid_b not in ids_a

    get_b = agent.get(f"/v1/sessions/{sid_b}")
    assert get_b.status_code == 200
    assert get_b.json().get("profile_id") == "enterprise"
