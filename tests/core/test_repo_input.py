"""Tests for resolving the repo list a run acts on (--input file or waves)."""
from __future__ import annotations

import pytest

from ado2gh.api import repo_input
from ado2gh.models import RepoConfig, WaveConfig


class _FakeConsole:
    """Records printed messages instead of rendering them, for assertions."""

    def __init__(self):
        self.messages = []

    def print(self, message):
        self.messages.append(message)


@pytest.fixture
def fake_console(monkeypatch):
    console = _FakeConsole()
    monkeypatch.setattr(repo_input, "console", console)
    return console


def test_load_repos_from_input_file_returns_loaded_repos(monkeypatch, fake_console):
    loaded = [RepoConfig(ado_project="p", ado_repo="r", gh_org="acme", gh_repo="r")]
    monkeypatch.setattr(
        "ado2gh.core.config_loader.ConfigLoader.load_input",
        lambda path, gh_org, default_scopes: loaded,
    )
    result = repo_input.load_repos("repos.txt", {"gh_org": "acme", "default_scopes": ["repo"]})
    assert result == loaded
    assert fake_console.messages == []


def test_load_repos_from_input_file_warns_when_none_found(monkeypatch, fake_console):
    monkeypatch.setattr(
        "ado2gh.core.config_loader.ConfigLoader.load_input",
        lambda path, gh_org, default_scopes: [],
    )
    result = repo_input.load_repos("repos.txt", {})
    assert result == []
    assert "No repos found in repos.txt" in fake_console.messages[0]


def test_load_repos_falls_back_to_waves_when_no_input_file(fake_console):
    repo = RepoConfig(ado_project="p", ado_repo="r", gh_org="acme", gh_repo="r")
    wave = WaveConfig(wave_id=1, name="w1", description="", repos=[repo])
    result = repo_input.load_repos("", {}, waves=[wave])
    assert result == [repo]
    assert fake_console.messages == []


def test_load_repos_warns_when_no_input_and_no_waves(fake_console):
    result = repo_input.load_repos("", {}, waves=None)
    assert result == []
    assert "No repos specified" in fake_console.messages[0]


def test_load_repos_warns_when_waves_have_no_repos(fake_console):
    wave = WaveConfig(wave_id=1, name="w1", description="", repos=[])
    result = repo_input.load_repos("", {}, waves=[wave])
    assert result == []
    assert "No repos specified" in fake_console.messages[0]
