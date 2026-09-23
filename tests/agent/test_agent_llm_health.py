"""Agent health reflects onboarded LLM models from shared ADO2GH_DATA_DIR."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.api.llm.llm_model_store import LLMModelStore
from services.agent.main import app


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_agent_health_llm_not_degraded_with_enabled_model(agent_client, tmp_path, monkeypatch):
    store = LLMModelStore()
    model = store.upsert(
        {
            "display_name": "Claude",
            "provider": "anthropic",
            "model_id": "claude-3-5-sonnet-20241022",
            "api_key": "sk-ant-test",
            "enabled": True,
            "default_for_agent": True,
            "validation_status": "passed",
            "validation_at": "2026-06-16T00:00:00Z",
        }
    )
    assert model.id

    with patch("services.agent.routes.run_routes._check_accelerator", new=AsyncMock(return_value=(True, None))):
        data = agent_client.get("/health").json()

    assert data["accelerator_reachable"] is True
    assert data["llm_degraded"] is False
    assert data["selected_model_id"] == model.id
    assert data["models_configured"] == 1


def test_agent_health_llm_degraded_without_models(agent_client):
    with patch("services.agent.routes.run_routes._check_accelerator", new=AsyncMock(return_value=(True, None))):
        data = agent_client.get("/health").json()

    assert data["llm_degraded"] is True
    assert data["llm_unconfigured"] is True
    assert data["models_configured"] == 0


def test_agent_session_prompts_when_no_llm_models(agent_client):
    created = agent_client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Hello", "dry_run": True},
    )
    assert created.status_code == 200
    body = created.json()
    assert body.get("llm_unconfigured") is True
    assistant = next((m for m in body.get("messages", []) if m.get("role") == "assistant"), None)
    assert assistant is not None
    assert "No LLM models are configured" in assistant["content"]
