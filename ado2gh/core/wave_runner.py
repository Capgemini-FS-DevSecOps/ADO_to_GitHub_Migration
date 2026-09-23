"""Wave-level orchestrator — delegates to BatchExecutor."""
from __future__ import annotations

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.logging_config import log
from ado2gh.models import ExecutionMode, WaveConfig
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.base import StateDBBase


class WaveRunner:
    """Execute all repository migrations within a single wave via BatchExecutor."""

    def __init__(self, engine: MigrationEngine, db: StateDBBase) -> None:
        """Build the batch executor this runner delegates every wave to.

        Args:
            engine: Per-repo migration engine the batch executor drives.
            db: State store the wave and its migration rows are recorded in.
        """
        self.engine = engine
        self.db = db
        self._executor = BatchExecutor(
            engine, db, ProgressTracker(total_repos=1, total_pipelines=1),
        )

    def run_wave(self, wave: WaveConfig,
                 mode: ExecutionMode = ExecutionMode.DRY_RUN) -> dict:
        """Run every repository in one wave through the batch executor.

        Args:
            wave: Wave to execute.
            mode: `ExecutionMode.DRY_RUN` (the default) simulates the wave;
                `ExecutionMode.LIVE` performs it (CA-001).

        Returns:
            The batch executor's wave summary.
        """
        log.info(
            "WaveRunner delegating wave %d (%d repos) to BatchExecutor",
            wave.wave_id, len(wave.repos),
        )
        return self._executor.execute_wave(wave, mode=mode)
