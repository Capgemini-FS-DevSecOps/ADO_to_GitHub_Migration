"""Tests for WaveRunner / BatchExecutor integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.wave_runner import WaveRunner
from ado2gh.models import RepoConfig, WaveConfig
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.db import StateDB


@pytest.fixture
def wave_config():
    return WaveConfig(
        wave_id=1,
        name="test-wave",
        description="test",
        repos=[
            RepoConfig(
                ado_project="proj",
                ado_repo="repo1",
                gh_org="ghorg",
                gh_repo="repo1",
                scopes=["repo"],
            ),
        ],
        parallel=1,
        pipeline_parallel=1,
    )


def test_wave_runner_accepts_engine_and_db(wave_config, tmp_path):
    db = StateDB(str(tmp_path / "test.db"))
    engine = MagicMock(spec=MigrationEngine)
    engine.migrate_repo.return_value = {"status": "completed", "scopes": {}, "errors": []}

    with patch.object(BatchExecutor, "execute_wave") as mock_exec:
        mock_exec.return_value = {
            "wave_id": 1, "status": "completed", "completed": 1,
            "failed": 0, "total": 1, "repos": {},
        }
        runner = WaveRunner(engine, db)
        summary = runner.run_wave(wave_config, dry_run=True)

    mock_exec.assert_called_once_with(wave_config, dry_run=True)
    assert summary["completed"] == 1
    assert summary["failed"] == 0


def test_batch_executor_execute_wave(wave_config, tmp_path):
    db = StateDB(str(tmp_path / "test.db"))
    engine = MagicMock(spec=MigrationEngine)
    engine.migrate_repo.return_value = {"status": "completed", "scopes": {}, "errors": []}
    executor = BatchExecutor(engine, db, ProgressTracker(1, 1))

    summary = executor.execute_wave(wave_config, dry_run=True)
    assert summary["wave_id"] == 1
    assert summary["completed"] == 1
    assert summary["failed"] == 0
