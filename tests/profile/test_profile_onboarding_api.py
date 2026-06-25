"""Profile onboarding API integration tests."""
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


def _bootstrap_admin(client: TestClient) -> None:
    r = client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    assert r.status_code == 201


def _register_operator(client: TestClient, username: str = "operator1") -> None:
    r = client.post(
        "/v1/auth/register",
        json={"username": username, "password": "twelve-char-pass", "display_name": "Op"},
    )
    assert r.status_code == 201
    users = client.get("/v1/auth/users").json()["users"]
    user_id = next(u["id"] for u in users if u["username"] == username)
    approve = client.post(f"/v1/auth/users/{user_id}/approve")
    assert approve.status_code == 200


def _valid_setup_payload(name: str = "Prod"):
    return {
        "name": name,
        "ado_org_url": "https://dev.azure.com/MYORG",
        "ado_pat": "pat-secret",
        "gh_org": "my-org",
        "github_token": "ghp_secret",
    }


@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_onboarding_status_zero_profiles(mock_gh, mock_ado, client):
    _bootstrap_admin(client)
    r = client.get("/v1/onboarding/status")
    assert r.status_code == 200
    data = r.json()
    assert data["needs_profile_setup"] is True
    assert data["active_profile_count"] == 0
    assert data["redirect_path"] == "/onboarding/profile"


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_setup_profile_admin_active(mock_gh, mock_ado, mock_scan, client):
    _bootstrap_admin(client)
    r = client.post("/v1/settings/profiles/setup", json=_valid_setup_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["is_default"] is True
    assert body["ado_pat"] == "***"


@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": False, "message": "bad ado"})
def test_setup_rejects_invalid_ado(mock_ado, client):
    _bootstrap_admin(client)
    r = client.post("/v1/settings/profiles/setup", json=_valid_setup_payload())
    assert r.status_code == 400


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_operator_blocked_without_active(mock_gh, mock_ado, mock_scan, client):
    _bootstrap_admin(client)
    _register_operator(client)
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "operator1", "password": "twelve-char-pass"})
    r = client.post("/v1/settings/profiles/setup", json=_valid_setup_payload("Op profile"))
    assert r.status_code == 403
    assert "operator_submit_blocked" in r.text


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_delete_last_active_blocked(mock_gh, mock_ado, mock_scan, client):
    _bootstrap_admin(client)
    created = client.post("/v1/settings/profiles/setup", json=_valid_setup_payload()).json()
    r = client.delete(f"/v1/settings/profiles/{created['id']}")
    assert r.status_code == 409


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_operator_onboarding_blocked_message(mock_gh, mock_ado, mock_scan, client):
    _bootstrap_admin(client)
    _register_operator(client, "op2")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op2", "password": "twelve-char-pass"})
    r = client.get("/v1/onboarding/status")
    data = r.json()
    assert data["needs_profile_setup"]
    assert data["can_submit_profile"] is False
    assert data["blocked_message"]


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_to_public_masks_secrets(mock_gh, mock_ado, mock_scan, client):
    _bootstrap_admin(client)
    created = client.post(
        "/v1/settings/profiles/setup",
        json={
            "name": "Masked",
            "ado_org_url": "https://dev.azure.com/MYORG",
            "ado_pat": "real-ado-pat",
            "gh_org": "org",
            "github_token": "ghp_real_token",
        },
    ).json()
    assert created["ado_pat"] == "***"
    assert "ghp_real" not in str(created)


@patch("services.accelerator_api.main.scan_with_credentials", return_value={"repos_scanned": 0})
@patch("services.accelerator_api.main.validate_ado_pat", return_value={"valid": True, "message": "ok"})
@patch("services.accelerator_api.main.validate_github_token", return_value={"valid": True, "message": "ok"})
def test_deactivate_admin_only(mock_gh, mock_ado, mock_scan, client):
    _bootstrap_admin(client)
    created = client.post("/v1/settings/profiles/setup", json=_valid_setup_payload()).json()
    _register_operator(client, "op3")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op3", "password": "twelve-char-pass"})
    r = client.post(f"/v1/settings/profiles/{created['id']}/deactivate")
    assert r.status_code == 403
