"""Contract tests for 004 agent PEV RBAC feature."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.agent.main import app as agent_app


CONTRACT_PATHS = [
    "specs/004-agent-pev-rbac/contracts/platform-rbac.md",
    "specs/004-agent-pev-rbac/contracts/live-approval-api.md",
    "specs/004-agent-pev-rbac/contracts/llm-model-api.md",
    "specs/004-agent-pev-rbac/contracts/agent-pev-ui-api.md",
]


def test_contract_files_exist():
    from pathlib import Path
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


def test_agent_health_remediation_contract():
    client = TestClient(agent_app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "accelerator_reachable" in data
    assert "llm_degraded" in data
    assert "remediation_steps" in data


def test_agent_session_create_contract_fields():
    client = TestClient(agent_app)
    r = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Plan POC", "dry_run": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"].startswith("ses_")
    assert "selected_model_id" in data
    assert "llm_degraded" in data


def test_llm_models_list_masks_secrets(accel_client):
    accel_client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    accel_client.post(
        "/v1/settings/llm-models",
        json={
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-secret-key",
        },
    )
    listed = accel_client.get("/v1/settings/llm-models")
    assert listed.status_code == 200
    model = listed.json()["models"][0]
    assert model["api_key"] == "***"
    assert "sk-secret" not in str(listed.json())


def test_platform_approvals_contract(accel_client):
    accel_client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    created = accel_client.post(
        "/v1/platform/approvals",
        json={"scope_type": "agent_session", "scope_id": "sess_c1", "reason_request": "test"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["id"].startswith("lve_")
    assert body["status"] == "pending"
    listed = accel_client.get("/v1/platform/approvals?status=pending")
    assert listed.status_code == 200
    assert "approvals" in listed.json()


def test_agent_llm_status_contract_fields():
    client = TestClient(agent_app)
    r = client.get("/v1/llm/status")
    assert r.status_code == 200
    data = r.json()
    assert "models_configured" in data
    assert "default_model_id" in data
