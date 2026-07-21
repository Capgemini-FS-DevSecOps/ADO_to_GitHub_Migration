"""FR-036 stale state recovery and concurrent live migration guards."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ado2gh.api.pipeline_runner import PipelineRunStore
from ado2gh.api.repo_lock import REPO_LOCK_MANAGER
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.migration_fr036 import clear_stale_in_progress_migrations, other_run_holds_repo
from ado2gh.models import MigrationScope, MigrationStatus, RepoConfig
from ado2gh.state.db import StateDB


@pytest.fixture(autouse=True)
def _reset_fr036_runtime_state():
    PipelineRunStore._runs.clear()
    REPO_LOCK_MANAGER._locks.clear()
    yield
    PipelineRunStore._runs.clear()
    REPO_LOCK_MANAGER._locks.clear()


@pytest.fixture
def db(tmp_path):
    return StateDB(str(tmp_path / "fr036.db"))


@pytest.fixture
def repo():
    return RepoConfig(
        ado_project="azure-pipelines",
        ado_repo="azure-pipelines-build-migration",
        gh_org="gh",
        gh_repo="azure-pipelines-build-migration",
        scopes=[MigrationScope.REPO.value],
    )


def test_clear_stale_in_progress_when_no_other_run(db, repo):
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    assert db.has_repo_in_progress(repo.ado_project, repo.ado_repo)

    cleared = clear_stale_in_progress_migrations(
        db,
        repo.ado_project,
        repo.ado_repo,
        current_run_id="run-new",
    )
    assert cleared == 1
    assert not db.has_repo_in_progress(repo.ado_project, repo.ado_repo)


def test_clear_stale_blocked_when_other_run_holds_lock(db, repo):
    repo_key = f"{repo.ado_project}/{repo.ado_repo}"
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    REPO_LOCK_MANAGER.acquire(repo_key, "run-other")

    try:
        cleared = clear_stale_in_progress_migrations(
            db,
            repo.ado_project,
            repo.ado_repo,
            current_run_id="run-new",
        )
        assert cleared == 0
        assert db.has_repo_in_progress(repo.ado_project, repo.ado_repo)
    finally:
        REPO_LOCK_MANAGER.release(repo_key, "run-other")


def test_clear_stale_allowed_when_current_run_holds_lock(db, repo):
    repo_key = f"{repo.ado_project}/{repo.ado_repo}"
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    REPO_LOCK_MANAGER.acquire(repo_key, "run-current")

    try:
        cleared = clear_stale_in_progress_migrations(
            db,
            repo.ado_project,
            repo.ado_repo,
            current_run_id="run-current",
        )
        assert cleared == 1
        assert not db.has_repo_in_progress(repo.ado_project, repo.ado_repo)
    finally:
        REPO_LOCK_MANAGER.release(repo_key, "run-current")


def test_other_run_holds_repo_ignores_current_active_run(repo):
    PipelineRunStore._runs.clear()
    active = PipelineRunStore.create(
        "active-run",
        dry_run=False,
        phase="poc",
        wave_id=None,
        repository_id="azure-pipelines/azure-pipelines-build-migration",
    )
    active.status = "running"
    repo_key = f"{repo.ado_project}/{repo.ado_repo}"

    assert not other_run_holds_repo(repo_key, current_run_id=active.id)
    assert other_run_holds_repo(repo_key, current_run_id="different-run")


def test_migration_engine_clears_stale_state_for_current_pipeline_run(db, repo):
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    repo_key = f"{repo.ado_project}/{repo.ado_repo}"
    REPO_LOCK_MANAGER.acquire(repo_key, "run-current")

    engine = MigrationEngine(
        {"migration_strategy": "mirror"},
        MagicMock(),
        MagicMock(),
        db,
        dry_run=False,
        pipeline_run_id="run-current",
    )
    with patch("ado2gh.core.migration_engine.SCOPE_REGISTRY") as reg:
        handler = MagicMock()
        handler.migrate.return_value = MagicMock(stats={}, failed=0)
        reg.get.return_value = handler
        result = engine.migrate_repo(1, repo)

    try:
        assert result["status"] == "completed"
        assert not db.has_repo_in_progress(repo.ado_project, repo.ado_repo)
    finally:
        REPO_LOCK_MANAGER.release(repo_key, "run-current")


def test_migration_engine_fr036_when_other_run_holds_lock(db, repo):
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    repo_key = f"{repo.ado_project}/{repo.ado_repo}"
    REPO_LOCK_MANAGER.acquire(repo_key, "run-other")

    engine = MigrationEngine(
        {"migration_strategy": "mirror"},
        MagicMock(),
        MagicMock(),
        db,
        dry_run=False,
        pipeline_run_id="run-current",
    )
    try:
        result = engine.migrate_repo(1, repo)
        assert result["status"] == "failed"
        assert any("FR-036" in err for err in result.get("errors", []))
    finally:
        REPO_LOCK_MANAGER.release(repo_key, "run-other")
