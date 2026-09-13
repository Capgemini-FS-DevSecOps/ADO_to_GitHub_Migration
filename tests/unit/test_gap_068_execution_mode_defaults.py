"""GAP-068: `MigrationEngine` and `rollback_wave` default to dry-run.

Both signatures used to declare `mode: ExecutionMode = ExecutionMode.LIVE`, so a
caller that omitted the argument migrated or deleted for real with nothing at the
call site to make the omission visible. Both now default to
`ExecutionMode.DRY_RUN`; live execution has to be asked for.

Approved by operator instruction, 2026-09-13 (operator-decisions.md § 1 settles
the same CA-001 policy for GAP-018, GAP-068 and GAP-078).

Every case uses fake clients, so a regression that restores the live default
fails here rather than pushing a git mirror or deleting a GitHub repository.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.rollback import RollbackHandler
from ado2gh.core.scopes import git_scope
from ado2gh.models import (
    ExecutionMode,
    MigrationScope,
    MigrationStatus,
    RepoConfig,
    WaveConfig,
)


@pytest.fixture
def repo():
    return RepoConfig(
        ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1",
        scopes=[MigrationScope.REPO.value],
    )


@pytest.fixture
def ado():
    """An ADO client that answers the reads the repo scope makes before it writes."""
    client = MagicMock()
    client.get_repo.return_value = {
        "id": "repo-id", "remoteUrl": "https://ado.example/_git/r1",
        "defaultBranch": "refs/heads/main", "size": 1024,
    }
    client.get_repo_stats.return_value = {"branch_count": 2, "tag_count": 0}
    return client


def _engine(ado_client=None, **kwargs):
    return MigrationEngine(
        {"migration_strategy": "mirror"},
        ado_client or MagicMock(),
        MagicMock(),
        MagicMock(),
        **kwargs,
    )


def _completed_repo_row():
    return {
        "status": MigrationStatus.COMPLETED.value,
        "scope": MigrationScope.REPO.value,
        "ado_project": "P", "ado_repo": "r1", "gh_org": "o", "gh_repo": "r1",
    }


def _rollback_fakes():
    gh, db = MagicMock(), MagicMock()
    db.get_wave_migrations.return_value = [_completed_repo_row()]
    wave = WaveConfig(wave_id=1, name="w1", description="", repos=[])
    return gh, db, wave


def test_engine_without_mode_is_dry_run():
    assert _engine().mode is ExecutionMode.DRY_RUN


def test_engine_mode_is_still_settable():
    assert _engine(mode=ExecutionMode.LIVE).mode is ExecutionMode.LIVE


def test_engine_without_mode_runs_no_git_command(monkeypatch, ado, repo):
    """The default engine reaches the repo scope but never shells out to git."""
    monkeypatch.setattr(
        git_scope.subprocess, "run",
        MagicMock(side_effect=AssertionError("git ran under the default mode")),
    )
    engine = _engine(ado)

    result = engine.migrate_repo(1, repo)

    assert result["status"] == "completed"
    assert result["scopes"]["repo"]["detail"]["dry_run"] is True
    engine.gh.create_private_repo.assert_not_called()
    engine.db.upsert_migration.assert_not_called()


def test_rollback_wave_without_mode_deletes_nothing():
    """The default rollback reports what it would undo and calls no GitHub write."""
    gh, db, wave = _rollback_fakes()

    stats = RollbackHandler(gh, db).rollback_wave(wave)

    assert stats["scopes_rolled_back"] == 1
    assert stats["errors"] == 0
    gh.delete_repo.assert_not_called()
    db.upsert_migration.assert_not_called()
    db.mark_wave_run.assert_not_called()


def test_rollback_wave_mode_is_still_settable():
    gh, db, wave = _rollback_fakes()

    RollbackHandler(gh, db).rollback_wave(wave, mode=ExecutionMode.LIVE)

    assert gh.delete_repo.called
    db.mark_wave_run.assert_called_once_with(1, "rolled_back")
