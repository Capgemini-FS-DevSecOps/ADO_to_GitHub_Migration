"""Operator profile approval flow tests."""
from unittest.mock import patch

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
    from services.accelerator_api.main import app, _settings

    db = create_state_db(str(db_path))
    auth_routes._svc = AuthService(db=db)
    _settings.path = tmp_path / "ui_settings.json"
    return TestClient(app)


def _bootstrap(client):
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def _register(client, name: str):
    client.post(
        "/v1/auth/register",
        json={"username": name, "password": "twelve-char-pass", "display_name": name},
    )


def _login(client, username: str):
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": username, "password": "twelve-char-pass"})


def _setup_payload(name: str):
    return {
        "name": name,
        "ado_org_url": "https://dev.azure.com/MYORG",
        "ado_pat": "pat",
        "gh_org": "org",
        "github_token": "ghp_test",
    }


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_operator_submit_pending_admin_approves(mock_gh, mock_ado, mock_scan, client):
    _bootstrap(client)
    client.post("/v1/settings/profiles/setup", json=_setup_payload("Admin profile"))
    _register(client, "operator")
    _login(client, "operator")
    pending = client.post("/v1/settings/profiles/setup", json=_setup_payload("Op profile")).json()
    assert pending["status"] == "pending_approval"
    scan = client.post(f"/v1/settings/profiles/{pending['id']}/scan")
    assert scan.status_code == 403
    _login(client, "admin")
    approved = client.post(f"/v1/settings/profiles/{pending['id']}/approve").json()
    assert approved["status"] == "active"


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_deny_and_appeal(mock_gh, mock_ado, mock_scan, client):
    _bootstrap(client)
    client.post("/v1/settings/profiles/setup", json=_setup_payload("Admin profile"))
    _register(client, "operator2")
    _login(client, "operator2")
    pending = client.post("/v1/settings/profiles/setup", json=_setup_payload("Denied")).json()
    _login(client, "admin")
    client.post(f"/v1/settings/profiles/{pending['id']}/deny", json={"reason": "bad"})
    _login(client, "operator2")
    denied = client.get("/v1/settings/profiles/mine/pending").json()
    assert any(p["status"] == "denied" for p in denied)
    appealed = client.post(f"/v1/settings/profiles/{pending['id']}/appeal").json()
    assert appealed["status"] == "pending_approval"
