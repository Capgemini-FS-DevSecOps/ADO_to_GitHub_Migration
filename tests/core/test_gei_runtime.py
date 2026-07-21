"""Tests for GEI/.NET runtime environment helpers."""
from __future__ import annotations

import os

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env, gei_subprocess_env


def test_ensure_gei_dotnet_env_sets_invariant(monkeypatch):
    monkeypatch.delenv("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", raising=False)
    ensure_gei_dotnet_env()
    assert os.environ["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] == "1"


def test_gei_subprocess_env_merges_extra(monkeypatch):
    monkeypatch.setenv("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT", "1")
    env = gei_subprocess_env(ADO_PAT="x", GH_PAT="y")
    assert env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] == "1"
    assert env["ADO_PAT"] == "x"
    assert env["GH_PAT"] == "y"
