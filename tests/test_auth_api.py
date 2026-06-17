"""Auth API contract tests."""
import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "auth_api.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def test_bootstrap_status_public(client):
    r = client.get("/v1/auth/bootstrap-status")
    assert r.status_code == 200
    assert "needs_bootstrap" in r.json()


def test_bootstrap_login_session_logout(client):
    r = client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    assert r.status_code == 201
    r2 = client.post("/v1/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert r2.status_code == 401
    assert r2.json()["detail"] == "Invalid credentials"
    r3 = client.post("/v1/auth/login", json={"username": "admin", "password": "twelve-char-pass"})
    assert r3.status_code == 200
    r4 = client.get("/v1/auth/session")
    assert r4.status_code == 200
    assert r4.json()["authenticated"]
    r5 = client.post("/v1/auth/logout")
    assert r5.status_code == 200
    r6 = client.get("/v1/auth/session")
    assert r6.status_code == 401


def test_admin_create_user(client):
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    r = client.post(
        "/v1/auth/users",
        json={
            "username": "op1",
            "password": "twelve-char-pass",
            "role": "operator",
            "display_name": "Op",
        },
    )
    assert r.status_code == 201
    listed = client.get("/v1/auth/users")
    assert any(u["username"] == "op1" for u in listed.json()["users"])
