"""`BatchExecutor` refuses a run whose engine disagrees with it about live.

The executor's `mode` decides what is reported and checkpointed; the engine's
own `mode` decides what is actually migrated. Nothing used to tie them together,
so a `LIVE` executor driving a `DRY_RUN` engine checkpointed and reported a
successful live run that migrated nothing, and the inverse pushed to GitHub
while reporting a dry run. Neither is visible in the returned summary.

Every in-repo caller already passes matching modes; this pins that they have to.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import ExecutionMode, PhaseType, RepoConfig, WaveConfig
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.progress_tracker import ProgressTracker

MISMATCHES = [
    (ExecutionMode.LIVE, ExecutionMode.DRY_RUN),
    (ExecutionMode.DRY_RUN, ExecutionMode.LIVE),
]


def _executor(engine_mode):
    engine = MagicMock(spec=MigrationEngine)
    engine.mode = engine_mode
    engine.migrate_repo.return_value = {"status": "completed", "scopes": {}, "errors": []}
    return BatchExecutor(engine, MagicMock(), ProgressTracker(1, 1)), engine


def _wave(phase=""):
    return WaveConfig(
        wave_id=1, name="w1", description="", phase=phase, parallel=1,
        repos=[RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")],
    )


@pytest.mark.parametrize(("asked", "engine_mode"), MISMATCHES)
def test_execute_wave_refuses_a_mismatch(asked, engine_mode):
    executor, engine = _executor(engine_mode)

    with pytest.raises(ValueError, match="execution mode mismatch"):
        executor.execute_wave(_wave(), mode=asked)

    engine.migrate_repo.assert_not_called()
    executor.db.mark_wave_run.assert_not_called()


@pytest.mark.parametrize(("asked", "engine_mode"), MISMATCHES)
def test_execute_phase_refuses_a_mismatch(asked, engine_mode):
    executor, engine = _executor(engine_mode)

    with pytest.raises(ValueError, match="execution mode mismatch"):
        executor.execute_phase(PhaseType.POC, [_wave(phase="poc")], mode=asked)

    engine.migrate_repo.assert_not_called()
    executor.db.upsert_batch_checkpoint.assert_not_called()


def test_the_message_names_both_modes():
    executor, _ = _executor(ExecutionMode.DRY_RUN)

    with pytest.raises(ValueError) as excinfo:
        executor.execute_wave(_wave(), mode=ExecutionMode.LIVE)

    assert "'live'" in str(excinfo.value)
    assert "'dry_run'" in str(excinfo.value)


def test_an_engine_with_no_mode_is_refused():
    """A stand-in that declares no mode cannot be proven to agree, so it is refused."""
    engine = MagicMock(spec=MigrationEngine)
    executor = BatchExecutor(engine, MagicMock(), ProgressTracker(1, 1))

    with pytest.raises(ValueError, match="execution mode mismatch"):
        executor.execute_wave(_wave(), mode=ExecutionMode.DRY_RUN)


@pytest.mark.parametrize("mode", [ExecutionMode.DRY_RUN, ExecutionMode.LIVE])
def test_matching_modes_run(mode):
    """Positive control: agreement on either mode reaches the engine."""
    executor, engine = _executor(mode)

    summary = executor.execute_wave(_wave(), mode=mode)

    assert summary["dry_run"] is (mode is ExecutionMode.DRY_RUN)
    assert engine.migrate_repo.call_count == 1
