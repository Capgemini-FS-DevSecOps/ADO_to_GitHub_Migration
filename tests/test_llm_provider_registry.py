"""Tests for LLM provider registry and extended platforms."""
from unittest.mock import MagicMock, patch

import pytest

from ado2gh.agents.llm_provider import (
    GeminiProvider,
    OpenAIProvider,
    build_provider_from_config,
    get_llm_provider,
)
from ado2gh.api.llm_model_store import LLMModelConfig
from ado2gh.api.llm_provider_registry import get_provider_spec, list_provider_specs
from ado2gh.api.model_catalog import list_catalog
from ado2gh.api.model_validation import validate_draft


def test_registry_includes_major_platforms():
    ids = {spec["id"] for spec in list_provider_specs()}
    assert "github_copilot" in ids
    assert "openrouter" in ids
    assert "azure_openai" in ids
    assert "google_gemini" in ids


def test_github_copilot_spec_uses_github_headers():
    spec = get_provider_spec("github_copilot")
    assert spec is not None
    assert "X-GitHub-Api-Version" in spec.runtime_headers()
    assert spec.default_base_url.endswith("/inference")


def test_build_github_copilot_provider():
    cfg = LLMModelConfig(
        id="1",
        display_name="Copilot",
        provider="github_copilot",
        model_id="openai/gpt-4o",
        api_key="ghp_test",
        enabled=True,
        validation_status="passed",
    )
    provider = build_provider_from_config(cfg)
    assert isinstance(provider, OpenAIProvider)
    assert provider.extra_headers["X-GitHub-Api-Version"]


def test_build_openrouter_provider():
    cfg = LLMModelConfig(
        id="2",
        display_name="OR",
        provider="openrouter",
        model_id="openai/gpt-4o",
        api_key="or-test",
        enabled=True,
        validation_status="passed",
    )
    provider = build_provider_from_config(cfg)
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url.endswith("/api/v1")


def test_build_gemini_provider():
    cfg = LLMModelConfig(
        id="3",
        display_name="Gemini",
        provider="google_gemini",
        model_id="gemini-2.0-flash",
        api_key="g-test",
        enabled=True,
        validation_status="passed",
    )
    provider = build_provider_from_config(cfg)
    assert isinstance(provider, GeminiProvider)


def test_get_llm_provider_github_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "github_copilot")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)


def test_openrouter_catalog_live_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [{"id": "openai/gpt-4o", "name": "GPT-4o"}],
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        result = list_catalog(provider="openrouter", api_key="or-test")
    assert result["source"] == "live"
    assert result["entries"][0]["id"] == "openai/gpt-4o"


def test_github_catalog_preset_fallback():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = RuntimeError("network down")
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        result = list_catalog(provider="github_copilot", api_key="ghp_test")
    assert result["source"] == "preset"
    assert result["stale"] is True
    assert result["entries"]


def test_validate_github_copilot_draft():
    with patch("ado2gh.api.model_validation._validate_openai_compatible") as mock_validate:
        result = validate_draft({
            "provider": "github_copilot",
            "model_id": "openai/gpt-4o",
            "api_key": "ghp_test",
        })
    assert result["status"] == "passed"
    mock_validate.assert_called_once()


def test_validate_azure_requires_base_url():
    result = validate_draft({
        "provider": "azure_openai",
        "model_id": "gpt-4o",
        "api_key": "azure-key",
    })
    assert result["status"] == "failed"
    assert "base URL" in result["message"]


def test_gemini_provider_parses_response():
    provider = GeminiProvider("g-test", "gemini-2.0-flash")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Gemini reply"}]}}],
    }
    with patch(
        "ado2gh.agents.llm_provider.build_llm_http_client",
        return_value=MagicMock(
            __enter__=MagicMock(return_value=MagicMock(post=MagicMock(return_value=mock_response))),
            __exit__=MagicMock(return_value=False),
        ),
    ):
        assert provider.complete("ping") == "Gemini reply"
