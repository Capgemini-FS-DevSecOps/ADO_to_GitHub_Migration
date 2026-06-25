"""Tests for local agent profile loader."""
import os

import pytest

from services.agent.profiles import get_profile, list_profile_ids, capability_matrix


def test_list_profile_ids():
    ids = list_profile_ids()
    assert "lightweight" in ids
    assert "full" in ids
    assert "prod-like" in ids


def test_get_lightweight_profile():
    p = get_profile("lightweight")
    assert p.lightweight_mode
    assert not p.auth_enabled
    assert p.dry_run_default
    assert p.llm_provider == "stub"


def test_env_override_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    p = get_profile("lightweight")
    assert p.llm_provider == "stub"


def test_capability_matrix_lightweight():
    p = get_profile("lightweight")
    caps = capability_matrix(p)
    assert caps["enqueue_job"] == "inline_stub"
    assert caps["auth"] == "disabled"


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        get_profile("nonexistent")
