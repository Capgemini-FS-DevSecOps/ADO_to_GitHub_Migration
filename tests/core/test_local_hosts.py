"""Tests for localhost URL rewriting for containerized API hosts."""
from __future__ import annotations

import httpx

from ado2gh.api.local_hosts import ollama_discovery_hint, resolve_local_service_url


def test_resolve_local_service_url_unchanged_without_alias(monkeypatch, tmp_path):
    monkeypatch.delenv("ADO2GH_LOCAL_HOST_ALIAS", raising=False)
    assert resolve_local_service_url("http://localhost:11434") == "http://localhost:11434"
    assert resolve_local_service_url("http://192.168.1.5:11434") == "http://192.168.1.5:11434"


def test_resolve_local_service_url_with_env_alias(monkeypatch):
    monkeypatch.setenv("ADO2GH_LOCAL_HOST_ALIAS", "host.docker.internal")
    assert resolve_local_service_url("http://localhost:11434") == "http://host.docker.internal:11434"
    assert resolve_local_service_url("http://127.0.0.1:11434") == "http://host.docker.internal:11434"


def test_ollama_discovery_hint_mentions_rewrite(monkeypatch):
    monkeypatch.setenv("ADO2GH_LOCAL_HOST_ALIAS", "host.docker.internal")
    hint = ollama_discovery_hint("http://localhost:11434")
    assert "host.docker.internal" in hint


def test_ollama_catalog_returns_discovery_error_on_connect_failure(monkeypatch):
    from ado2gh.api.llm.model_catalog import list_catalog

    monkeypatch.delenv("ADO2GH_LOCAL_HOST_ALIAS", raising=False)

    def _raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("ado2gh.api.llm.model_catalog.build_llm_http_client", lambda **kwargs: _FakeClient(_raise_connect_error))

    result = list_catalog(provider="ollama", base_url="http://localhost:11434")
    assert result["entries"] == []
    assert "discovery_error" in result
    assert "Ollama" in result["discovery_error"]


class _FakeClient:
    def __init__(self, on_get):
        self._on_get = on_get

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        return self._on_get(*args, **kwargs)
