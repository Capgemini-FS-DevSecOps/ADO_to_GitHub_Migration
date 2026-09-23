"""Regression check for register entry GAP-130.

`plan_revision_key` bound the operator's approval to the configuration file's
NAME (`config_path`) but not its contents, while execution reads destinations
and organisation defaults from the file's contents. Editing the file between
approval and execution — without renaming it — left the approval in place for
a configuration the operator never saw.
"""
from __future__ import annotations

from types import SimpleNamespace

from ado2gh.agents.migration_agent.hitl.blockers import plan_revision_key
from ado2gh.api.settings_store import SettingsStore


def _plan() -> dict:
    return {"revision": 1, "dry_run": True, "repos": [{"id": "Proj/app"}]}


def _patch_config_path(monkeypatch, config_path: str) -> None:
    monkeypatch.setattr(
        SettingsStore,
        "load",
        lambda self: SimpleNamespace(advanced=SimpleNamespace(config_path=config_path)),
    )


def test_editing_the_configuration_file_changes_the_plan_revision_key(monkeypatch, tmp_path):
    """Same file name, different bytes — the approval must not survive the edit."""
    config_file = tmp_path / "migration.yaml"
    config_file.write_text("org: acme\n")
    _patch_config_path(monkeypatch, str(config_file))

    key_before = plan_revision_key(_plan())

    config_file.write_text("org: other-org\n")
    key_after = plan_revision_key(_plan())

    assert key_before != key_after


def test_an_unreadable_or_missing_configuration_file_never_raises(monkeypatch, tmp_path):
    """No config file yet (or it was deleted) still yields a stable, non-raising key."""
    missing = tmp_path / "does-not-exist.yaml"
    _patch_config_path(monkeypatch, str(missing))

    key = plan_revision_key(_plan())

    assert isinstance(key, str)
    assert key


def test_missing_configuration_file_differs_from_a_present_one_with_the_same_name(monkeypatch, tmp_path):
    """The fingerprint, not just the presence of a path string, drives identity."""
    config_file = tmp_path / "migration.yaml"
    _patch_config_path(monkeypatch, str(config_file))
    key_missing = plan_revision_key(_plan())

    config_file.write_text("org: acme\n")
    key_present = plan_revision_key(_plan())

    assert key_missing != key_present


def test_restoring_the_original_contents_restores_the_original_key(monkeypatch, tmp_path):
    """The fingerprint is a pure function of the bytes, not a one-way trip."""
    config_file = tmp_path / "migration.yaml"
    original = "org: acme\n"
    config_file.write_text(original)
    _patch_config_path(monkeypatch, str(config_file))

    key_original = plan_revision_key(_plan())

    config_file.write_text("org: other-org\n")
    assert plan_revision_key(_plan()) != key_original

    config_file.write_text(original)
    assert plan_revision_key(_plan()) == key_original
