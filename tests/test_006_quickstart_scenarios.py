"""Quickstart scenario integration tests for feature 006."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "quickstart.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _admin(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def test_scenario1_catalog_validate_enable(client, monkeypatch):
    _admin(client)
    mock_catalog_response = MagicMock()
    mock_catalog_response.raise_for_status = MagicMock()
    mock_catalog_response.json.return_value = {
        "data": [{"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"}],
        "has_more": False,
    }
    mock_catalog_client = MagicMock()
    mock_catalog_client.__enter__ = MagicMock(return_value=mock_catalog_client)
    mock_catalog_client.__exit__ = MagicMock(return_value=False)
    mock_catalog_client.get.return_value = mock_catalog_response

    mock_validate_response = MagicMock()
    mock_validate_response.raise_for_status = MagicMock()
    mock_validate_client = MagicMock()
    mock_validate_client.__enter__ = MagicMock(return_value=mock_validate_client)
    mock_validate_client.__exit__ = MagicMock(return_value=False)
    mock_validate_client.post.return_value = mock_validate_response

    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_catalog_client):
        catalog = client.get(
            "/v1/settings/llm-models/catalog",
            params={"provider": "anthropic", "api_key": "sk-ant-test"},
        )
    assert catalog.status_code == 200
    entry = catalog.json()["entries"][0]

    with patch("ado2gh.api.model_validation.build_llm_http_client", return_value=mock_validate_client):
        validated = client.post(
            "/v1/settings/llm-models/validate",
            json={
                "provider": "anthropic",
                "model_id": entry["id"],
                "api_key": "sk-ant-test",
            },
        )
    assert validated.json()["status"] == "passed"

    created = client.post(
        "/v1/settings/llm-models",
        json={
            "display_name": "Claude",
            "provider": "anthropic",
            "model_id": entry["id"],
            "api_key": "sk-ant-test",
            "catalog_source": "live",
            "catalog_label": entry["display_name"],
            "enabled": True,
            "validation_status": "passed",
            "validation_at": validated.json()["validated_at"],
        },
    )
    assert created.status_code == 200
    assert created.json()["enabled"] is True


def test_scenario2_openai_preset_fallback(client, monkeypatch):
    _admin(client)
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = RuntimeError("network blocked")
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        catalog = client.get(
            "/v1/settings/llm-models/catalog",
            params={"provider": "openai", "api_key": "sk-test"},
        )
    data = catalog.json()
    assert data["source"] == "preset"
    assert data["stale"] is True


def test_scenario5_enable_blocked_without_validation(client):
    _admin(client)
    created = client.post(
        "/v1/settings/llm-models",
        json={
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
            "enabled": True,
        },
    )
    assert created.status_code == 400


def test_scenario7_operator_denied_models_and_connectivity(client):
    _admin(client)
    client.post(
        "/v1/auth/users",
        json={
            "username": "op1",
            "password": "twelve-char-pass",
            "role": "operator",
            "display_name": "Op",
        },
    )
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op1", "password": "twelve-char-pass"})
    assert client.get("/v1/settings/llm-models/catalog", params={"provider": "anthropic"}).status_code == 403
    assert client.get("/v1/settings/connectivity").status_code == 403


def test_scenario8_connectivity_and_validation_audit_redaction(client, tmp_path, monkeypatch):
    _admin(client)
    put = client.put(
        "/v1/settings/connectivity",
        json={"proxy_enabled": True, "proxy_host": "proxy", "proxy_password": "top-secret-proxy"},
    )
    assert put.status_code == 200
    assert "top-secret-proxy" not in str(put.json())

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    with patch("ado2gh.api.model_validation.build_llm_http_client", return_value=mock_client):
        validated = client.post(
            "/v1/settings/llm-models/validate",
            json={"provider": "stub", "model_id": "stub"},
        )
    assert validated.status_code == 200
    db = create_state_db(str(tmp_path / "quickstart.db"))
    events = db.list_audit_events(limit=20)
    blob = str(events)
    assert "top-secret-proxy" not in blob
    assert "llm.model.validated" in blob or "connectivity.updated" in blob
