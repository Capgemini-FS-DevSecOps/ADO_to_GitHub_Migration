"""Concurrent platform auth session isolation."""
import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "concurrent.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _bootstrap(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def _login(client: TestClient, username: str) -> TestClient:
    c = TestClient(client.app)
    c.post("/v1/auth/login", json={"username": username, "password": "twelve-char-pass"})
    return c


def test_two_sessions_independent_logout(client):
    _bootstrap(client)
    client.post(
        "/v1/auth/users",
        json={"username": "op1", "password": "twelve-char-pass", "role": "operator", "display_name": "Op"},
    )
    admin = _login(client, "admin")
    operator = _login(client, "op1")
    assert admin.get("/v1/auth/session").json()["user"]["role"] == "admin"
    assert operator.get("/v1/auth/session").json()["user"]["role"] == "operator"
    operator.post("/v1/auth/logout")
    assert operator.get("/v1/auth/session").status_code == 401
    assert admin.get("/v1/auth/session").status_code == 200


def test_audit_live_approval_has_username_not_local_developer(client, tmp_path):
    _bootstrap(client)
    created = client.post(
        "/v1/platform/approvals",
        json={"scope_type": "agent_session", "scope_id": "sess_audit", "reason_request": "live"},
    )
    approval_id = created.json()["id"]
    approved = client.post(
        f"/v1/platform/approvals/{approval_id}/approve",
        json={"reason": "ok for test"},
    )
    assert approved.status_code == 200
    import os
    db = create_state_db(os.environ["ADO2GH_SQLITE_PATH"])
    events = db.list_audit_events(profile_id="_platform", limit=30)
    live_events = [e for e in events if "live_execution" in (e.get("event_type") or "")]
    assert live_events
    assert all(e.get("actor") != "local-developer" for e in live_events)
