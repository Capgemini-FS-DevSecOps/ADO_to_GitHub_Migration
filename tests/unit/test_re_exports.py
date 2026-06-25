"""Test that re-exports from decomposed modules still work."""
from __future__ import annotations

import pytest


def test_session_orchestrator_re_exports():
    """Legacy session_orchestrator deleted (spec 012)."""
    pytest.skip("Legacy session_orchestrator deleted (spec 012)")


def test_pipeline_runner_re_exports():
    """from ado2gh.api.pipeline_runner import * should still work."""
    mod = __import__("ado2gh.api.pipeline_runner", fromlist=["*"])
    assert mod is not None


def test_settings_store_re_exports():
    """from ado2gh.api.settings_store import * should still work."""
    mod = __import__("ado2gh.api.settings_store", fromlist=["*"])
    assert mod is not None
