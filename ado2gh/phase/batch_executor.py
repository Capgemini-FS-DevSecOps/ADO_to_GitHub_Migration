"""Sub-batch execution with SQLite checkpointing and resume."""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TimeElapsedColumn,
)

from ado2gh.logging_config import console, log
from ado2gh.models import (
    DEFAULT_PHASES,
    BatchCheckpoint,
    ExecutionMode,
    PhaseType,
    RepoConfig,
    WaveConfig,
)
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.base import StateDBBase

if TYPE_CHECKING:
    from ado2gh.core.migration_engine import MigrationEngine


class BatchExecutor:
    """Run the repositories of a wave or phase through the migration engine in batches.

    A phase is split into fixed-size batches; each batch is checkpointed in the
    state DB so an interrupted run resumes after the last completed batch.
    Nothing is checkpointed or recorded as a wave run in ``DRY_RUN`` mode, so a
    dry run never makes a later live run skip work.
    """

    def __init__(self, engine: MigrationEngine, db: StateDBBase, tracker: ProgressTracker) -> None:
        """Bind the engine that migrates one repo, the state DB and the velocity tracker.

        Args:
            engine: Performs the per-repository migration (``migrate_repo``).
            db: State store for wave runs and batch checkpoints.
            tracker: Records completed repos for the velocity and ETA readout.
        """
        self.engine = engine
        self.db = db
        self.tracker = tracker

    def execute_phase(
        self,
        phase: PhaseType,
        waves: list[WaveConfig],
        mode: ExecutionMode = ExecutionMode.LIVE,
    ) -> dict:
        """Migrate every repo assigned to ``phase``, batch by batch, resuming from checkpoints.

        Args:
            phase: The phase whose waves are executed.
            waves: All configured waves; only those whose ``phase`` matches are run.
            mode: ``DRY_RUN`` previews without checkpointing; ``LIVE`` migrates
                and checkpoints each batch.

        Returns:
            Summary with ``phase``, ``completed``, ``failed``, ``batches_run`` and
            ``batches_skipped`` counts.
        """
        phase_waves = [w for w in waves if w.phase == phase.value]
        if not phase_waves:
            console.print(f"[yellow]No waves for phase {phase.value}[/yellow]")
            return {"phase": phase.value, "completed": 0, "failed": 0,
                    "batches_run": 0, "batches_skipped": 0}

        dry_run = mode is ExecutionMode.DRY_RUN
        cfg = DEFAULT_PHASES[phase]
        all_repos = [r for w in phase_waves for r in w.repos]
        batches = [all_repos[i:i + cfg.batch_size]
                   for i in range(0, len(all_repos), cfg.batch_size)]
        total_b = len(batches)
        last_done = self.db.get_last_completed_batch(phase)
        base_wid = min(w.wave_id for w in phase_waves)

        console.print(Panel(
            f"[bold]Phase: {phase.value.upper()}[/bold]\n"
            f"Repos: {len(all_repos)} | Batches: {total_b} (size={cfg.batch_size})\n"
            f"Repo parallel: {cfg.repo_parallel} | Pipeline parallel: {cfg.pipeline_parallel}\n"
            + (f"Resuming from batch {last_done + 2}/{total_b}"
               if last_done >= 0 else "Starting fresh")
            + f" | Dry-run: {dry_run}",
            border_style="blue", title=f"Phase {phase.value.upper()}",
        ))

        summary: dict[str, Any] = {"phase": phase.value, "completed": 0, "failed": 0,
                   "batches_run": 0, "batches_skipped": last_done + 1}

        for batch_num, batch_repos in enumerate(batches):
            if batch_num <= last_done:
                log.info(f"  Batch {batch_num + 1}/{total_b}: skipped (already done)")
                continue

            cp = BatchCheckpoint(
                phase=phase, batch_num=batch_num, total_batches=total_b,
                repos_done=0, repos_total=len(batch_repos), status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            # Don't persist checkpoints during a dry-run — otherwise a
            # subsequent real run would skip the phase as already completed.
            if not dry_run:
                self.db.upsert_batch_checkpoint(cp)

            wave_cfg = WaveConfig(
                wave_id=base_wid + batch_num,
                name=f"{phase.value.upper()}-b{batch_num + 1}",
                description=f"Batch {batch_num + 1}/{total_b}",
                repos=batch_repos, parallel=cfg.repo_parallel,
                pipeline_parallel=cfg.pipeline_parallel, phase=phase.value,
            )
            console.print(
                f"\n[cyan]Batch {batch_num + 1}/{total_b}[/cyan] "
                f"({len(batch_repos)} repos)")
            b = self._run_batch(wave_cfg, mode)
            summary["completed"] += b["completed"]
            summary["failed"] += b["failed"]
            summary["batches_run"] += 1
            for _ in range(b["completed"]):
                self.tracker.record_repo()

            cp.status = "completed" if b["failed"] == 0 else "partial"
            cp.repos_done = b["completed"]
            cp.completed_at = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                self.db.upsert_batch_checkpoint(cp)

            snap = self.tracker.snapshot(self.db)
            eta = snap['eta_str'] or 'calculating...'
            console.print(
                f"  Batch {batch_num + 1} done: {b['completed']} ok, {b['failed']} failed | "
                f"Overall {snap['done_repos']}/{snap['total_repos']} "
                f"({snap['pct_complete']}%) | "
                f"Velocity {snap['repo_velocity']} repos/min | "
                f"ETA {eta}"
            )
        return summary

    def execute_wave(
        self,
        wave: WaveConfig,
        mode: ExecutionMode = ExecutionMode.LIVE,
        cancel_event: threading.Event | None = None,
        on_repo_done: Callable[[str, dict, RepoConfig], None] | None = None,
    ) -> dict:
        """Execute a single wave (used by ``ado2gh run``).

        Args:
            wave: The wave to run.
            mode: ``DRY_RUN`` previews; ``LIVE`` migrates and records the wave run.
            cancel_event: When set, no further repos are submitted and pending
                ones are cancelled.
            on_repo_done: Called with ``"project/repo"``, the repo's result dict
                and its ``RepoConfig`` as each repo finishes.

        Returns:
            Wave summary: ``wave_id``, ``name``, ``status`` (``completed``,
            ``partial`` or ``failed``), ``dry_run``, per-repo ``repos`` results
            and the ``completed`` / ``failed`` / ``partial`` / ``total`` counts.
        """
        dry_run = mode is ExecutionMode.DRY_RUN
        log.info(
            "=== Starting wave %d: %s (%d repos)%s ===",
            wave.wave_id, wave.name, len(wave.repos),
            " [DRY RUN]" if dry_run else "",
        )
        batch = self._run_batch(
            wave, mode, cancel_event=cancel_event, on_repo_done=on_repo_done,
        )
        statuses = batch.get("repo_statuses", {})
        completed = batch["completed"]
        failed = batch["failed"]
        overall = (
            "completed" if failed == 0 and completed == len(wave.repos)
            else ("partial" if completed > 0 else "failed")
        )
        return {
            "wave_id": wave.wave_id,
            "name": wave.name,
            "status": overall,
            "dry_run": dry_run,
            "repos": statuses,
            "completed": completed,
            "failed": failed,
            "partial": len(wave.repos) - completed - failed,
            "total": len(wave.repos),
        }

    def _run_batch(
        self,
        wave: WaveConfig,
        mode: ExecutionMode,
        scopes_filter: list[str] | None = None,
        cancel_event: threading.Event | None = None,
        on_repo_done: Callable[[str, dict, RepoConfig], None] | None = None,
    ) -> dict:
        """Migrate the repos of one wave in parallel and record the wave run.

        Args:
            wave: The wave whose repos are submitted to the engine.
            mode: In ``LIVE`` mode the wave run is marked started and
                completed/partial in the state DB; ``DRY_RUN`` records nothing.
            scopes_filter: When given, every repo runs with exactly these scopes.
            cancel_event: When set, stops submitting and cancels pending repos.
            on_repo_done: Per-repo completion callback (see ``execute_wave``).

        Returns:
            ``completed`` and ``failed`` counts plus ``repo_statuses`` keyed by
            ``"project/repo"``.
        """
        live = mode is ExecutionMode.LIVE
        if live:
            self.db.mark_wave_run(wave.wave_id, "started", mode)
        result: dict[str, Any] = {"completed": 0, "failed": 0, "repo_statuses": {}}
        with Progress(SpinnerColumn(), "[progress.description]{task.description}",
                      BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
                      console=console, transient=True) as prog:
            task = prog.add_task(wave.name, total=len(wave.repos))
            with ThreadPoolExecutor(max_workers=wave.parallel) as pool:
                futures = {}
                for repo in wave.repos:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    repo_to_run = repo
                    if scopes_filter:
                        from dataclasses import replace
                        repo_to_run = replace(repo, scopes=scopes_filter)
                    futures[pool.submit(
                        self.engine.migrate_repo, wave.wave_id, repo_to_run,
                        prog, task, pipeline_parallel=wave.pipeline_parallel,
                    )] = repo
                for future in as_completed(futures):
                    if cancel_event is not None and cancel_event.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    repo = futures[future]
                    key = f"{repo.ado_project}/{repo.ado_repo}"
                    try:
                        res = future.result(timeout=1800)
                        result["repo_statuses"][key] = res
                        if res.get("errors"):
                            result["failed"] += 1
                        else:
                            result["completed"] += 1
                        if on_repo_done:
                            on_repo_done(key, res, repo)
                    except Exception as e:
                        log.error(f"Batch error [{repo.ado_repo}]: {e}")
                        result["failed"] += 1
                        result["repo_statuses"][key] = {
                            "status": "failed", "error": str(e),
                        }
                        if on_repo_done:
                            on_repo_done(key, result["repo_statuses"][key], repo)
        if live:
            self.db.mark_wave_run(
                wave.wave_id,
                "completed" if result["failed"] == 0 else "partial",
            )
        return result
