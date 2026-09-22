"""Unit tests for the shared CORS origin reader in ado2gh/api/service_settings.py.

Both accelerator and agent FastAPI apps used to read and split CORS_ORIGINS
independently, with different fallback defaults. This covers the one shared
reader they now both call through, checking each app keeps its own default.
"""
from __future__ import annotations

import pytest

from ado2gh.api.service_settings import DEFAULT_CORS_ORIGIN, cors_origins


@pytest.fixture(autouse=True)
def _clear_cors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)


def test_default_uses_module_constant_when_unset() -> None:
    """With no caller-supplied default and no environment variable, the module constant wins."""
    assert cors_origins() == [DEFAULT_CORS_ORIGIN]


def test_caller_supplied_default_used_when_unset() -> None:
    """A caller's own default (e.g. the accelerator's "*") is honoured when unset."""
    assert cors_origins(default="*") == ["*"]


def test_splits_comma_separated_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """CORS_ORIGINS is split on commas into a list, regardless of which default was requested."""
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,https://b.example.com")
    assert cors_origins() == ["https://a.example.com", "https://b.example.com"]
    assert cors_origins(default="*") == ["https://a.example.com", "https://b.example.com"]


def test_single_origin_no_comma(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single configured origin comes back as a one-element list."""
    monkeypatch.setenv("CORS_ORIGINS", "https://only.example.com")
    assert cors_origins() == ["https://only.example.com"]
