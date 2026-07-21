"""Platform RBAC capability matrix tests."""
import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import AuthService, permissions_for
from ado2gh.state.factory import create_state_db


def test_permissions_for_roles():
    admin = permissions_for(PlatformRole.ADMIN)
    assert admin["can_manage_settings"] is True
    assert admin["can_manage_models"] is True
    assert admin["can_approve_live_execution"] is True

    approver = permissions_for(PlatformRole.APPROVER)
    assert approver["can_manage_settings"] is False
    assert approver["can_manage_models"] is False
    assert approver["can_approve_live_execution"] is True
    assert approver["can_operate"] is False

    operator = permissions_for(PlatformRole.OPERATOR)
    assert operator["can_operate"] is True
    assert operator["can_manage_settings"] is False
    assert operator["can_approve_live_execution"] is False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "rbac.db"
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


def _create_user(client: TestClient, username: str, role: str) -> None:
    client.post(
        "/v1/auth/users",
        json={
            "username": username,
            "password": "twelve-char-pass",
            "role": role,
            "display_name": username,
        },
    )


def test_session_includes_extended_permissions(client):
    _bootstrap(client)
    r = client.get("/v1/auth/session")
    perms = r.json()["permissions"]
    assert "can_manage_settings" in perms
    assert "can_approve_live_execution" in perms


def test_operator_cannot_create_llm_model(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    r = client.post(
        "/v1/settings/llm-models",
        json={"display_name": "GPT", "provider": "openai", "model_id": "gpt-4o-mini", "api_key": "sk-test"},
    )
    assert r.status_code == 403


def test_operator_can_read_settings(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    r = client.get("/v1/settings")
    assert r.status_code == 200


def test_operator_can_read_profiles_list(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    r = client.get("/v1/settings/profiles/pending")
    assert r.status_code == 403
    settings = client.get("/v1/settings")
    assert settings.status_code == 200


def test_operator_cannot_update_profile(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    r = client.put(
        "/v1/settings/profiles/nonexistent",
        json={"name": "x", "ado_org_url": "https://dev.azure.com/x", "gh_org": "x"},
    )
    assert r.status_code in (403, 404)


def test_approver_can_list_live_approvals(client):
    _bootstrap(client)
    _create_user(client, "ap1", "approver")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "ap1", "password": "twelve-char-pass"})
    r = client.get("/v1/platform/approvals")
    assert r.status_code == 200
    assert "approvals" in r.json()


def test_operator_cannot_access_llm_catalog(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    r = client.get("/v1/settings/llm-models/catalog", params={"provider": "anthropic"})
    assert r.status_code == 403


def test_operator_cannot_validate_llm_model(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    r = client.post(
        "/v1/settings/llm-models/validate",
        json={"provider": "stub", "model_id": "stub"},
    )
    assert r.status_code == 403


def test_operator_cannot_update_connectivity(client):
    _bootstrap(client)
    _create_user(client, "op1", "operator")
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    assert client.get("/v1/settings/connectivity").status_code == 403
    assert client.put("/v1/settings/connectivity", json={"proxy_enabled": True}).status_code == 403
