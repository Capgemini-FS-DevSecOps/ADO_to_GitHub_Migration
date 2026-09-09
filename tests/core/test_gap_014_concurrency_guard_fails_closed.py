"""GAP-014 (GAP-ENG-07) — the FR-036 concurrency guard must fail closed.

Reproduction from the gap register: ``other_run_holds_repo`` in
``ado2gh/core/conflict_detection.py`` asks two independent sources whether another
run owns the repo — ``REPO_LOCK_MANAGER.holder(...)`` at ``:14-21`` and
``PipelineRunStore.list_active_runs()`` at ``:22-33``. Both are wrapped in
``except Exception: pass`` with no record that the check was inconclusive, so
when *both* raise, execution falls through to ``return False`` at ``:34`` — the
answer that means "no other run holds this repo". ``clear_stale_in_progress_
migrations`` at ``:46-47`` only declines to clear when that call returns True, so
the double-exception path clears the stale in-progress row, and
``migration_engine.py:72-78`` treats a cleared row as permission to start a
second live migration of the same repo.

The tests below make both conflict sources raise a transient error. Detection has
failed, so the guard must not answer "no conflict": the stale row must survive
and the migration must be refused. On current code the guard answers False, the
row is cleared and the migration proceeds — so these tests fail, which is the
point.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ado2gh.api.pipeline_runner import PipelineRunStore
from ado2gh.api.repo_lock import REPO_LOCK_MANAGER
from ado2gh.core.conflict_detection import clear_stale_in_progress_migrations, other_run_holds_repo
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import ExecutionMode, MigrationScope, MigrationStatus, RepoConfig
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
    return StateDB(str(tmp_path / "gap014.db"))


@pytest.fixture
def repo():
    return RepoConfig(
        ado_project="azure-pipelines",
        ado_repo="azure-pipelines-build-migration",
        gh_org="gh",
        gh_repo="azure-pipelines-build-migration",
        scopes=[MigrationScope.REPO.value],
    )


def _both_conflict_checks_failing():
    """Context manager making both of the guard's own sources raise transiently."""
    return (
        patch.object(
            REPO_LOCK_MANAGER,
            "holder",
            side_effect=RuntimeError("lock manager unavailable"),
        ),
        patch(
            "ado2gh.api.pipeline_store.PipelineRunStore.list_active_runs",
            side_effect=RuntimeError("pipeline run store unavailable"),
        ),
    )


def test_guard_does_not_report_no_conflict_when_both_checks_fail(repo):
    """With both conflict sources erroring, the guard must not answer False —
    "detection failed" is not "no other run holds this repo"."""
    repo_key = f"{repo.ado_project}/{repo.ado_repo}"
    lock_patch, store_patch = _both_conflict_checks_failing()

    with lock_patch, store_patch:
        answer = other_run_holds_repo(repo_key, current_run_id="run-new")

    assert answer is not False, (
        "other_run_holds_repo returned False after both of its own conflict "
        "checks raised: inconclusive detection was reported as 'no conflict'"
    )


def test_stale_row_not_cleared_when_conflict_detection_is_inconclusive(db, repo):
    """A stale in_progress row must survive when conflict detection failed —
    clearing it is what authorises a second live migration."""
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)
    lock_patch, store_patch = _both_conflict_checks_failing()

    with lock_patch, store_patch:
        cleared = clear_stale_in_progress_migrations(
            db,
            repo.ado_project,
            repo.ado_repo,
            current_run_id="run-new",
        )

    assert cleared == 0, (
        f"cleared {cleared} in_progress row(s) while conflict detection was inconclusive"
    )
    assert db.has_repo_in_progress(repo.ado_project, repo.ado_repo), (
        "the stale in_progress row was cleared even though no source could confirm "
        "the repo was free"
    )


def test_migrate_repo_refuses_when_conflict_detection_is_inconclusive(db, repo):
    """The engine must refuse a live migration of a repo carrying an in_progress
    row when it could not establish that no other run holds it."""
    db.upsert_migration(1, repo, "repo", MigrationStatus.IN_PROGRESS)

    engine = MigrationEngine(
        {"migration_strategy": "mirror"},
        MagicMock(),
        MagicMock(),
        db,
        mode=ExecutionMode.LIVE,
        pipeline_run_id="run-new",
    )

    lock_patch, store_patch = _both_conflict_checks_failing()
    with lock_patch, store_patch, patch(
        "ado2gh.core.migration_engine.SCOPE_REGISTRY"
    ) as reg:
        handler = MagicMock()
        handler.migrate.return_value = MagicMock(stats={}, failed=0)
        reg.get.return_value = handler
        result = engine.migrate_repo(1, repo)

    assert result["status"] == "failed", (
        f"live migration proceeded with status {result['status']!r} while conflict "
        "detection was inconclusive"
    )
    assert any("FR-036" in err for err in result.get("errors", [])), (
        f"no FR-036 conflict error reported; got {result.get('errors')!r}"
    )
