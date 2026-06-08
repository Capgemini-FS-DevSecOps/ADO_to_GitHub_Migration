"""Wave-level orchestrator — delegates to BatchExecutor."""
from __future__ import annotations

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.logging_config import log
from ado2gh.models import WaveConfig
from ado2gh.phase.batch_executor import BatchExecutor
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.db import StateDB


class WaveRunner:
    """Execute all repository migrations within a single wave via BatchExecutor."""

    def __init__(self, engine: MigrationEngine, db: StateDB):
        self.engine = engine
        self.db = db
        self._executor = BatchExecutor(
            engine, db, ProgressTracker(total_repos=1, total_pipelines=1),
        )

    def run_wave(self, wave: WaveConfig, dry_run: bool = False) -> dict:
        log.info(
            "WaveRunner delegating wave %d (%d repos) to BatchExecutor",
            wave.wave_id, len(wave.repos),
        )
        return self._executor.execute_wave(wave, dry_run=dry_run)
