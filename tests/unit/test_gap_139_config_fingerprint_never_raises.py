"""Regression check for register entry GAP-139.

`_config_content_fingerprint` (`hitl/blockers.py`) is an identity input for
`plan_revision_key` and must never raise — it only caught `OSError`, which
covers a missing or unreadable file, but not a path holding an embedded NUL
byte (`ValueError` from `Path.read_bytes()`) or a non-string path
(`TypeError`). Either would have made the whole approval-key calculation
raise instead of degrading to the empty fingerprint like every other
unreadable-path case.
"""
from __future__ import annotations

from types import SimpleNamespace

from ado2gh.agents.migration_agent.hitl.blockers import (
    _config_content_fingerprint,
    plan_revision_key,
)
from ado2gh.api.settings_store import SettingsStore


def _plan() -> dict:
    return {"revision": 1, "dry_run": True, "repos": [{"id": "Proj/app"}]}


def test_path_with_embedded_nul_byte_returns_empty_fingerprint():
    assert _config_content_fingerprint("bad\x00path.yaml") == ""


def test_non_string_path_returns_empty_fingerprint():
    assert _config_content_fingerprint(12345) == ""  # type: ignore[arg-type]


def test_plan_revision_key_never_raises_on_a_nul_byte_config_path(monkeypatch):
    monkeypatch.setattr(
        SettingsStore,
        "load",
        lambda self: SimpleNamespace(advanced=SimpleNamespace(config_path="bad\x00path.yaml")),
    )

    key = plan_revision_key(_plan())

    assert isinstance(key, str)
    assert key
