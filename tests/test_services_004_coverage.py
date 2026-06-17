"""Targeted coverage for services/agent and services/accelerator_api (T059)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db


@pytest.fixture
def accel_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "svc.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


@pytest.fixture
def agent_client(monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "false")
    from services.agent.main import app as agent_app
    return TestClient(agent_app)


def test_accelerator_health_and_auth_routes(accel_client):
    assert accel_client.get("/health").status_code == 200
    assert accel_client.get("/ready").status_code == 200
    assert accel_client.get("/v1/auth/bootstrap-status").status_code == 200
    accel_client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    assert accel_client.get("/v1/auth/session").status_code == 200
    assert accel_client.get("/v1/onboarding/status").status_code == 200
    assert accel_client.get("/v1/settings").status_code == 200


def test_accelerator_platform_and_llm_routes(accel_client):
    accel_client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    accel_client.post(
        "/v1/settings/llm-models",
        json={
            "display_name": "Stub",
            "provider": "stub",
            "model_id": "stub",
            "api_key": "",
        },
    )
    assert accel_client.get("/v1/settings/llm-models").status_code == 200
    created = accel_client.post(
        "/v1/platform/approvals",
        json={"scope_type": "agent_session", "scope_id": "s1", "reason_request": "t"},
    )
    assert created.status_code == 200
    aid = created.json()["id"]
    assert accel_client.get("/v1/platform/approvals?status=pending").status_code == 200
    assert accel_client.get(f"/v1/platform/approvals/{aid}").status_code == 200


def test_accelerator_migrate_dry_run(accel_client):
    accel_client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    with patch("ado2gh.api.accelerator.Accelerator.run_wave") as rw:
        from ado2gh.api.contracts import RunWaveResult
        rw.return_value = RunWaveResult(
            wave_id=1, status="completed", completed=1, failed=0, total=1, dry_run=True,
        )
        r = accel_client.post(
            "/v1/migrate",
            json={"config_path": "migration.yaml", "dry_run": True, "wave_id": 1},
        )
    assert r.status_code == 200


def test_agent_health_llm_and_session(agent_client):
    assert agent_client.get("/health").status_code == 200
    assert agent_client.get("/v1/llm/status").status_code == 200
    assert agent_client.get("/v1/mcp/tools").status_code == 200
    with patch("services.agent.main._accel_post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [
            {"waves": []},
            {"auto": 0},
            [{"wave_id": 1}],
            {"total": 0},
        ]
        r = agent_client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "prompt": "hi", "dry_run": True, "execute_pev": True},
        )
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert agent_client.get(f"/v1/sessions/{sid}").status_code == 200
    assert agent_client.post(
        f"/v1/sessions/{sid}/message", json={"message": "follow up"},
    ).status_code == 200


def test_agent_request_live_offline_fallback(agent_client):
    import httpx

    with patch("services.agent.main._accel_post", new_callable=AsyncMock) as mock_post:
        created = agent_client.post(
            "/v1/sessions",
            json={"profile_id": "lightweight", "dry_run": True},
        )
        sid = created.json()["session_id"]
        mock_post.side_effect = httpx.ConnectError("offline")
        r = agent_client.post(f"/v1/sessions/{sid}/request-live")
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_approval"


def test_agent_deprecated_approvals_list(agent_client):
    with pytest.warns(DeprecationWarning):
        r = agent_client.get("/v1/approvals")
    assert r.status_code == 200
    assert r.json() == []
