"""Tests for LLM model validation service."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ado2gh.api.llm.model_validation import (
    AgentCapabilityProbeError,
    _infer_capabilities,
    _require_agent_capabilities,
    validate_draft,
    validate_saved,
)
from ado2gh.api.llm.llm_model_store import LLMModelStore


def _agent_capable_openai_payload() -> dict:
    return {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "agent_capability_probe",
                        "arguments": '{"ready": true}',
                    },
                }],
            },
        }],
    }


def _mock_http_client(payload: dict | None = None) -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = payload or _agent_capable_openai_payload()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    return mock_client


def test_validate_stub_passes():
    result = validate_draft(
        {"provider": "stub", "model_id": "stub", "display_name": "Stub"},
    )
    assert result["status"] == "passed"
    assert result["capabilities"]["supports_tool_calling"] is True
    assert "sk-" not in result["message"]


def test_infer_capabilities_accepts_native_tool_calls():
    caps = _infer_capabilities("openai", _agent_capable_openai_payload())
    assert caps is not None
    assert caps["supports_tool_calling"] is True


def test_infer_capabilities_accepts_agent_json():
    payload = {
        "choices": [{
            "message": {
                "content": (
                    '{"thinking":"ready","tool_calls":[{"name":"agent_capability_probe",'
                    '"arguments":{"ready":true}}]}'
                ),
            },
        }],
    }
    caps = _infer_capabilities("openai", payload)
    assert caps is not None
    assert caps["supports_tool_calling"] is True
    assert caps["supports_thinking"] is True


def test_require_agent_capabilities_rejects_plain_text():
    payload = {"choices": [{"message": {"content": "hello"}}]}
    with pytest.raises(AgentCapabilityProbeError, match="agent reasoning or tool use"):
        _require_agent_capabilities("openai", payload)


def test_validate_anthropic_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "content": [{
            "type": "tool_use",
            "id": "tool_1",
            "name": "agent_capability_probe",
            "input": {"ready": True},
        }],
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    with patch("ado2gh.api.llm.model_validation.build_llm_http_client", return_value=mock_client):
        result = validate_draft(
            {
                "provider": "anthropic",
                "model_id": "claude-3-5-sonnet-20241022",
                "api_key": "sk-ant-test",
            }
        )
    assert result["status"] == "passed"
    assert result["capabilities"]["supports_tool_calling"] is True


def test_validate_ollama_success():
    with patch(
        "ado2gh.api.llm.model_validation.build_llm_http_client",
        return_value=_mock_http_client(),
    ):
        result = validate_draft(
            {
                "provider": "ollama",
                "model_id": "qwen2.5",
                "base_url": "http://localhost:11434",
            }
        )
    assert result["status"] == "passed"


def test_validate_agent_incapable():
    payload = {"choices": [{"message": {"content": "pong"}}]}
    with patch(
        "ado2gh.api.llm.model_validation.build_llm_http_client",
        return_value=_mock_http_client(payload),
    ):
        result = validate_draft(
            {
                "provider": "openai",
                "model_id": "text-embedding-3-small",
                "api_key": "sk-test",
            }
        )
    assert result["status"] == "failed"
    assert result["category"] == "agent_incapable"


def test_validate_timeout_category():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = httpx.TimeoutException("slow")
    with patch("ado2gh.api.llm.model_validation.build_llm_http_client", return_value=mock_client):
        result = validate_draft(
            {
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key": "sk-test",
            }
        )
    assert result["category"] == "timeout"


def test_validate_openai_uses_cloud_http_client():
    mock_client = _mock_http_client()
    with patch("ado2gh.api.llm.model_validation.build_llm_http_client") as mock_factory:
        mock_factory.return_value = mock_client
        validate_draft(
            {
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key": "sk-test",
            }
        )
        mock_factory.assert_called_with(for_cloud=True)


def test_audit_payloads_contain_no_raw_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "audit.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app
    from ado2gh.auth.service import AuthService
    from ado2gh.state.factory import create_state_db

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    client = TestClient(app)
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    secret = "sk-super-secret-key-value"
    with patch(
        "ado2gh.api.llm.model_validation.build_llm_http_client",
        return_value=_mock_http_client(),
    ):
        response = client.post(
            "/v1/settings/llm-models/validate",
            json={"provider": "openai", "model_id": "gpt-4o-mini", "api_key": secret},
        )
    assert response.status_code == 200
    assert secret not in response.json()["message"]
    db = create_state_db(str(db_path))
    events = db.list_audit_events(limit=50)
    assert secret not in str(events)
    client.put(
        "/v1/settings/connectivity",
        json={"proxy_password": "proxy-secret-password", "proxy_enabled": True, "proxy_host": "p"},
    )
    events = db.list_audit_events(limit=50)
    assert "proxy-secret-password" not in str(events)


def test_validate_anthropic_model_not_found_category():
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {
        "type": "error",
        "error": {
            "type": "not_found_error",
            "message": "model: claude-3-5-sonnet-20241022",
        },
    }
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad model",
        request=MagicMock(),
        response=mock_response,
    )
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    with patch("ado2gh.api.llm.model_validation.build_llm_http_client", return_value=mock_client):
        result = validate_draft(
            {
                "provider": "anthropic",
                "model_id": "claude-3-5-sonnet-20241022",
                "api_key": "sk-ant-test",
            }
        )
    assert result["category"] == "model_not_found"


def test_validate_openai_success():
    with patch(
        "ado2gh.api.llm.model_validation.build_llm_http_client",
        return_value=_mock_http_client(),
    ):
        result = validate_draft(
            {
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key": "sk-secret-key-value",
            }
        )
    assert result["status"] == "passed"
    assert "sk-secret" not in result["message"]


def test_validate_credentials_failure_category():
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "unauthorized",
        request=MagicMock(),
        response=mock_response,
    )
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    with patch("ado2gh.api.llm.model_validation.build_llm_http_client", return_value=mock_client):
        result = validate_draft(
            {
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key": "sk-bad",
            }
        )
    assert result["status"] == "failed"
    assert result["category"] == "credentials"


def test_concurrent_duplicate_validate_completes_without_hang():
    body = {
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "api_key": "sk-concurrent",
    }
    with patch(
        "ado2gh.api.llm.model_validation.build_llm_http_client",
        return_value=_mock_http_client(),
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(validate_draft, body) for _ in range(2)]
            results = [f.result(timeout=30) for f in futures]
    assert all(r["status"] == "passed" for r in results)


def test_validate_saved_updates_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = LLMModelStore()
    model = store.upsert(
        {
            "display_name": "GPT",
            "provider": "stub",
            "model_id": "stub",
        }
    )
    result = validate_saved(model.id, store=store)
    assert result["status"] == "passed"
    reloaded = store.get(model.id)
    assert reloaded.validation_status == "passed"
    assert reloaded.capabilities["supports_tool_calling"] is True
