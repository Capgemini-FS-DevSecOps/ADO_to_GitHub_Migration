"""Contract tests for 006 LLM model catalog feature."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

CONTRACT_PATHS = [
    "specs/006-llm-model-catalog/contracts/llm-catalog-api.md",
    "specs/006-llm-model-catalog/contracts/connectivity-api.md",
    "specs/006-llm-model-catalog/contracts/llm-models-ui.md",
]


def test_contract_files_exist():
    root = Path(__file__).resolve().parents[2]
    for rel in CONTRACT_PATHS:
        assert (root / rel).is_file(), rel


@pytest.fixture
def accel_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "contract.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _bootstrap_admin(client: TestClient) -> None:
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )


def test_connectivity_get_contract(accel_client):
    _bootstrap_admin(accel_client)
    r = accel_client.get("/v1/settings/connectivity")
    assert r.status_code == 200
    data = r.json()
    assert "proxy_enabled" in data
    assert "allow_custom_model_id" in data
    assert data.get("proxy_password", "") in ("", "***")


def test_catalog_anthropic_preset_contract(accel_client):
    _bootstrap_admin(accel_client)
    r = accel_client.get(
        "/v1/settings/llm-models/catalog",
        params={"provider": "anthropic"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "preset"
    assert data["stale"] is False
    assert len(data["entries"]) >= 1
    entry = data["entries"][0]
    assert "id" in entry
    assert "display_name" in entry


def test_validate_draft_contract_shape(accel_client, monkeypatch):
    _bootstrap_admin(accel_client)

    def _fake_validate(body, **_kwargs):
        return {
            "status": "passed",
            "category": None,
            "message": "Model responded successfully.",
            "validated_at": "2026-06-16T18:00:00Z",
        }

    monkeypatch.setattr(
        "ado2gh.api.model_validation.validate_draft",
        _fake_validate,
    )
    r = accel_client.post(
        "/v1/settings/llm-models/validate",
        json={
            "display_name": "GPT-4o Mini",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
            "catalog_source": "live",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("passed", "failed")
    assert "validated_at" in data


def test_connectivity_put_contract(accel_client, monkeypatch):
    _bootstrap_admin(accel_client)
    r = accel_client.put(
        "/v1/settings/connectivity",
        json={
            "proxy_enabled": True,
            "proxy_host": "proxy.corp",
            "proxy_port": 8080,
            "proxy_password": "secret-pass",
            "allow_custom_model_id": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["proxy_enabled"] is True
    assert data["allow_custom_model_id"] is True
    assert data["proxy_password"] == "***"
    assert "secret-pass" not in str(data)


def test_connectivity_test_contract(accel_client, monkeypatch):
    _bootstrap_admin(accel_client)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    monkeypatch.setattr(
        "ado2gh.api.http_llm.build_llm_http_client",
        lambda **kwargs: mock_client,
    )
    r = accel_client.post("/v1/settings/connectivity/test")
    assert r.status_code == 200
    assert r.json()["status"] == "passed"


def test_list_models_extended_fields(accel_client):
    _bootstrap_admin(accel_client)
    r = accel_client.get("/v1/settings/llm-models")
    assert r.status_code == 200
    assert "models" in r.json()
