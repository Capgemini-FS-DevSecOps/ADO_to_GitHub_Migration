"""LLM provider adapter tests (OpenAI + Anthropic + Ollama + stub)."""
from unittest.mock import MagicMock, patch

import pytest

from ado2gh.agents.llm_provider import (
    AnthropicProvider,
    OllamaProvider,
    OpenAIProvider,
    StubLLMProvider,
    get_llm_provider,
)


def _mock_http_client(response: MagicMock):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = response
    return mock_client


def test_stub_provider_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ADO2GH_LLM_BACKEND", raising=False)
    monkeypatch.delenv("ADO2GH_LLM_MODEL_ID", raising=False)
    provider = get_llm_provider()
    text = provider.complete("hello world")
    assert "hello" in text.lower() or "[stub]" in text


def test_openai_provider_parses_response():
    provider = OpenAIProvider("sk-test", "gpt-4o-mini")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "OpenAI reply"}}],
    }
    with patch(
        "ado2gh.agents.llm_provider.build_llm_http_client",
        return_value=_mock_http_client(mock_response),
    ):
        assert provider.complete("ping") == "OpenAI reply"


def test_anthropic_provider_parses_response():
    provider = AnthropicProvider("sk-ant", "claude-3-5-sonnet-20241022")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "content": [{"type": "text", "text": "Anthropic reply"}],
    }
    with patch(
        "ado2gh.agents.llm_provider.build_llm_http_client",
        return_value=_mock_http_client(mock_response),
    ):
        assert provider.complete("ping") == "Anthropic reply"


def test_ollama_provider_parses_response():
    provider = OllamaProvider("http://localhost:11434", "qwen2.5:latest")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"message": {"content": "Ollama reply"}}
    with patch(
        "ado2gh.agents.llm_provider.build_llm_http_client",
        return_value=_mock_http_client(mock_response),
    ) as mock_factory:
        assert provider.complete("ping") == "Ollama reply"
        mock_factory.assert_called_with(for_cloud=False)


def test_get_llm_provider_openai_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)


def test_get_llm_provider_anthropic_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    provider = get_llm_provider()
    assert isinstance(provider, AnthropicProvider)


def test_get_llm_provider_from_model_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    store.save([])
    store.upsert({
        "display_name": "Claude",
        "provider": "anthropic",
        "model_id": "claude-3-5-sonnet-20241022",
        "api_key": "sk-ant-test",
        "enabled": True,
        "validation_status": "passed",
        "validation_at": "2026-06-16T00:00:00Z",
    })
    models = store.load()
    monkeypatch.setenv("ADO2GH_LLM_MODEL_ID", models[0].id)
    provider = get_llm_provider()
    assert isinstance(provider, AnthropicProvider)


def test_get_llm_provider_ollama_from_model_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    model = store.upsert({
        "display_name": "Local Qwen",
        "provider": "ollama",
        "model_id": "qwen2.5:latest",
        "base_url": "http://localhost:11434",
        "enabled": True,
        "validation_status": "passed",
        "validation_at": "2026-06-16T00:00:00Z",
    })
    monkeypatch.setenv("ADO2GH_LLM_MODEL_ID", model.id)
    provider = get_llm_provider()
    assert isinstance(provider, OllamaProvider)


def test_unvalidated_model_falls_back_to_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    model = store.upsert({
        "display_name": "GPT",
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "api_key": "sk-test",
    })
    assert model.validation_status == "never_validated"
    monkeypatch.setenv("ADO2GH_LLM_MODEL_ID", model.id)
    provider = get_llm_provider()
    assert not isinstance(provider, (OpenAIProvider, AnthropicProvider, OllamaProvider))
