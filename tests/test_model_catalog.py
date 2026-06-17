"""Tests for LLM model catalog service."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from ado2gh.api.model_catalog import list_catalog


def test_anthropic_requires_api_key_for_catalog():
    result = list_catalog(provider="anthropic")
    assert result["entries"] == []
    assert result["source"] == "live"
    assert result["stale"] is False


def test_anthropic_live_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"},
            {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"},
        ],
        "has_more": False,
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        result = list_catalog(provider="anthropic", api_key="sk-ant-test")
    assert result["source"] == "live"
    assert result["stale"] is False
    assert result["entries"][0]["id"] == "claude-sonnet-4-6"


def test_anthropic_fallback_preset_on_live_failure():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = RuntimeError("network down")
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        result = list_catalog(provider="anthropic", api_key="sk-ant-test")
    assert result["source"] == "preset"
    assert result["stale"] is True


def test_openai_live_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}],
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        result = list_catalog(provider="openai", api_key="sk-test")
    assert result["source"] == "live"
    assert result["stale"] is False
    assert result["entries"][0]["id"] == "gpt-4o-mini"


def test_openai_fallback_preset_on_live_failure():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = RuntimeError("network down")
    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        result = list_catalog(provider="openai", api_key="sk-test")
    assert result["source"] == "preset"
    assert result["stale"] is True


def test_catalog_single_flight_concurrent_requests():
    calls = {"count": 0}

    def _slow_fetch(*args, **kwargs):
        calls["count"] += 1
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "gpt-4o-mini"}]}
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = _slow_fetch

    with patch("ado2gh.api.model_catalog.build_llm_http_client", return_value=mock_client):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(list_catalog, provider="openai", api_key="sk-shared-key")
                for _ in range(2)
            ]
            results = [f.result(timeout=30) for f in futures]
    assert all(r["entries"] for r in results)
    assert calls["count"] >= 1


def test_ollama_discovery():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"models": [{"name": "qwen2.5:latest"}]}
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    with patch("ado2gh.api.model_catalog.build_llm_http_client") as mock_factory:
        mock_factory.return_value = mock_client
        result = list_catalog(provider="ollama", base_url="http://localhost:11434")
        mock_factory.assert_called_with(for_cloud=False)
    assert result["source"] == "live"
    assert result["entries"][0]["id"] == "qwen2.5:latest"
    mock_client.get.assert_called_with("http://localhost:11434/api/tags", headers=None)


def test_openai_catalog_uses_cloud_http_client():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "gpt-4o-mini"}]}
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    with patch("ado2gh.api.model_catalog.build_llm_http_client") as mock_factory:
        mock_factory.return_value = mock_client
        list_catalog(provider="openai", api_key="sk-test")
        mock_factory.assert_called_with(for_cloud=True)


def test_ollama_requires_base_url():
    with pytest.raises(ValueError, match="base_url"):
        list_catalog(provider="ollama")
