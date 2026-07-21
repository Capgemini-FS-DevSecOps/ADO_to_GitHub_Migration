"""Operator self-registration tests."""
import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService, SESSION_COOKIE
from ado2gh.state.factory import create_state_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    db = create_state_db(str(db_path))
    auth_routes._svc = AuthService(db=db)
    return TestClient(app)


def _bootstrap(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def _user_id(client: TestClient, username: str) -> str:
    users = client.get("/v1/auth/users").json()["users"]
    return next(u["id"] for u in users if u["username"] == username)


def test_register_operator_after_bootstrap(client):
    _bootstrap(client)
    r = client.post(
        "/v1/auth/register",
        json={"username": "newop", "password": "twelve-char-pass", "display_name": "Op"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["pending_approval"] is True
    assert body["user"]["role"] == "operator"
    assert body["user"]["status"] == "pending_approval"
    assert SESSION_COOKIE not in r.cookies

    status = client.get("/v1/auth/bootstrap-status").json()
    assert status["registration_enabled"] is True
    assert status["pending_user_approvals"] == 1


def test_pending_operator_cannot_login(client):
    _bootstrap(client)
    client.post(
        "/v1/auth/register",
        json={"username": "newop", "password": "twelve-char-pass", "display_name": "Op"},
    )
    r = client.post(
        "/v1/auth/login",
        json={"username": "newop", "password": "twelve-char-pass"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "account_pending_approval"


def test_admin_approves_operator_login(client):
    _bootstrap(client)
    client.post(
        "/v1/auth/register",
        json={"username": "newop", "password": "twelve-char-pass", "display_name": "Op"},
    )
    uid = _user_id(client, "newop")
    approved = client.post(f"/v1/auth/users/{uid}/approve")
    assert approved.status_code == 200
    assert approved.json()["user"]["status"] == "active"

    r = client.post(
        "/v1/auth/login",
        json={"username": "newop", "password": "twelve-char-pass"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "newop"


def test_admin_can_disable_and_reenable_user(client):
    _bootstrap(client)
    client.post(
        "/v1/auth/users",
        json={
            "username": "op1",
            "password": "twelve-char-pass",
            "role": "operator",
            "display_name": "Op",
        },
    )
    uid = _user_id(client, "op1")
    client.post("/v1/auth/logout")
    assert client.post(
        "/v1/auth/login",
        json={"username": "op1", "password": "twelve-char-pass"},
    ).status_code == 200

    client.post("/v1/auth/login", json={"username": "admin", "password": "twelve-char-pass"})
    disabled = client.post(f"/v1/auth/users/{uid}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["user"]["status"] == "disabled"

    client.post("/v1/auth/logout")
    blocked = client.post(
        "/v1/auth/login",
        json={"username": "op1", "password": "twelve-char-pass"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "account_disabled"

    client.post("/v1/auth/login", json={"username": "admin", "password": "twelve-char-pass"})
    enabled = client.patch(f"/v1/auth/users/{uid}", json={"status": "active"})
    assert enabled.status_code == 200
    client.post("/v1/auth/logout")
    assert client.post(
        "/v1/auth/login",
        json={"username": "op1", "password": "twelve-char-pass"},
    ).status_code == 200


def test_register_blocked_before_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "isolated.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    client = TestClient(app)
    r = client.post(
        "/v1/auth/register",
        json={"username": "early", "password": "twelve-char-pass", "display_name": "Early"},
    )
    assert r.status_code == 403


def test_register_duplicate_generic_error(client):
    _bootstrap(client)
    body = {"username": "dup", "password": "twelve-char-pass", "display_name": "Dup"}
    assert client.post("/v1/auth/register", json=body).status_code == 201
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 400
    assert r.json()["detail"] == "Registration failed"


def test_register_operator_service_audit(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    svc = AuthService()
    svc.bootstrap_admin("admin", "twelve-char-pass", "Admin")
    svc.register_operator("operator", "twelve-char-pass", "Op")
    db_obj = svc.db
    events = db_obj.list_audit_events(profile_id="_platform", limit=10)
    assert any(e.get("event_type") == "user.registered" for e in events)
