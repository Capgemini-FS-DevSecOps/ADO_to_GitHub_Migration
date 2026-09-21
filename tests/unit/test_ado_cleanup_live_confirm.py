"""A live `ado-cleanup` with repos to act on now requires an explicit confirmation first.

`ado-cleanup --live` disables ADO pipelines and can archive the ADO repository —
the same class of one-way action `rollback` already gates with `click.confirm`,
which was until now the only confirmation prompt in `ado2gh/`. Nothing asked
before `ado-cleanup` ran, so a mistyped `--live` (the flag that opts into a real
run, added under a separate, earlier register entry; register cross-reference:
GAP-018) took effect immediately with no chance to back out. Requiring an explicit approval before a
real, one-way action runs is a standing safeguard across this codebase
(register cross-reference: CA-002).

The prompt is skipped when there is nothing to act on, so a `--live` run over an
empty repo list — the case `tests/unit/test_gap_018_dry_run_default.py::
test_ado_cleanup_dry_run_default` already pins — stays non-interactive.
"""
from __future__ import annotations

from click.testing import CliRunner

import ado2gh.core.ado_cleanup as cleanup_module
import ado2gh.core.config_loader as config_loader_module
from ado2gh.cli import misc
from ado2gh.cli.main import cli
from ado2gh.models import RepoConfig

REPO = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")


class _FakeCleanup:
    """Stands in for `ADOCleanup`, recording whether it was ever asked to act."""

    calls: list[list[RepoConfig]] = []

    def __init__(self, ado, *, mode):
        self.ado = ado
        self.mode = mode

    def cleanup_repos(self, repos, *, archive_repo):
        self.__class__.calls.append(repos)


def _run(monkeypatch, argv, *, input_text=None, repos=(REPO,)):
    _FakeCleanup.calls = []
    monkeypatch.setattr(
        config_loader_module.ConfigLoader, "load", staticmethod(lambda _path: ({}, [])),
    )
    monkeypatch.setattr(misc, "load_clients", lambda *_a, **_kw: (object(), object()))
    monkeypatch.setattr(misc, "load_repos", lambda *_a, **_kw: list(repos))
    monkeypatch.setattr(cleanup_module, "ADOCleanup", _FakeCleanup)
    return CliRunner().invoke(cli, argv, input=input_text)


def test_live_with_repos_confirmed_runs_cleanup(monkeypatch):
    result = _run(monkeypatch, ["ado-cleanup", "-c", "migration.yaml", "--live"], input_text="y\n")

    assert result.exit_code == 0, result.output
    assert _FakeCleanup.calls == [[REPO]]


def test_live_with_repos_declined_aborts_without_running_cleanup(monkeypatch):
    result = _run(monkeypatch, ["ado-cleanup", "-c", "migration.yaml", "--live"], input_text="n\n")

    assert result.exit_code == 1
    assert _FakeCleanup.calls == []


def test_dry_run_never_prompts_even_with_repos(monkeypatch):
    """The default previews without touching stdin at all (no `input=`)."""
    result = _run(monkeypatch, ["ado-cleanup", "-c", "migration.yaml"])

    assert result.exit_code == 0, result.output
    assert _FakeCleanup.calls == [[REPO]]


def test_live_with_no_repos_does_not_prompt(monkeypatch):
    """Nothing to act on is nothing to confirm (keeps the empty-repos case from an
    earlier, separate register entry non-interactive; register cross-reference: GAP-018).
    """
    result = _run(monkeypatch, ["ado-cleanup", "-c", "migration.yaml", "--live"], repos=())

    assert result.exit_code == 0, result.output
    assert _FakeCleanup.calls == [[]]
