"""Operator self-registration tests."""
import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
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


def test_register_operator_after_bootstrap(client):
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    r = client.post(
        "/v1/auth/register",
        json={"username": "newop", "password": "twelve-char-pass", "display_name": "Op"},
    )
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "operator"
    status = client.get("/v1/auth/bootstrap-status").json()
    assert status["registration_enabled"] is True


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
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
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
