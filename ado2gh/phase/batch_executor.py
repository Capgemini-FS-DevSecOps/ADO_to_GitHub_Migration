"""Sub-batch execution with SQLite checkpointing and resume."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TimeElapsedColumn,
)

from ado2gh.logging_config import console, log
from ado2gh.models import (
    DEFAULT_PHASES, BatchCheckpoint, PhaseType, RepoConfig, WaveConfig,
)
from ado2gh.phase.progress_tracker import ProgressTracker
from ado2gh.state.db import StateDB


class BatchExecutor:
    def __init__(self, engine, db: StateDB, tracker: ProgressTracker):
        self.engine = engine
        self.db = db
        self.tracker = tracker

    def execute_phase(self, phase: PhaseType, waves: list[WaveConfig],
                      dry_run: bool = False) -> dict:
        phase_waves = [w for w in waves if w.phase == phase.value]
        if not phase_waves:
            console.print(f"[yellow]No waves for phase {phase.value}[/yellow]")
            return {"phase": phase.value, "completed": 0, "failed": 0,
                    "batches_run": 0, "batches_skipped": 0}

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

        summary = {"phase": phase.value, "completed": 0, "failed": 0,
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
            b = self._run_batch(wave_cfg, dry_run)
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
        dry_run: bool = False,
        cancel_event=None,
        on_repo_done: Callable[[str, dict, RepoConfig], None] | None = None,
    ) -> dict:
        """Execute a single wave (used by ``ado2gh run``)."""
        log.info(
            "=== Starting wave %d: %s (%d repos)%s ===",
            wave.wave_id, wave.name, len(wave.repos),
            " [DRY RUN]" if dry_run else "",
        )
        batch = self._run_batch(
            wave, dry_run, cancel_event=cancel_event, on_repo_done=on_repo_done,
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

    def retry_failed_pipelines(self, wave: WaveConfig, dry_run: bool = False) -> dict:
        """Reset and re-run pipeline migrations for a wave."""
        failed = self.db.get_failed_pipeline_migrations(wave.wave_id)
        if not failed:
            return {"retried": 0, "completed": 0, "failed": 0}
        if not dry_run:
            self.db.reset_failed_pipeline_migrations(wave.wave_id)

        repos_by_key: dict[tuple, Any] = {}
        for row in failed:
            key = (row["project"], row["repo_name"])
            if key not in repos_by_key:
                for r in wave.repos:
                    if r.ado_project == row["project"] and r.ado_repo == row["repo_name"]:
                        repos_by_key[key] = r
                        break

        retry_repos = list(repos_by_key.values())
        if not retry_repos:
            return {"retried": 0, "completed": 0, "failed": len(failed)}

        narrow_wave = WaveConfig(
            wave_id=wave.wave_id,
            name=f"{wave.name}-pipeline-retry",
            description="Pipeline retry",
            repos=retry_repos,
            parallel=wave.parallel,
            pipeline_parallel=wave.pipeline_parallel,
            phase=wave.phase,
        )
        result = self._run_batch(narrow_wave, dry_run, scopes_filter=["pipelines"])
        return {
            "retried": len(failed),
            "completed": result["completed"],
            "failed": result["failed"],
        }

    def _run_batch(
        self,
        wave: WaveConfig,
        dry_run: bool,
        scopes_filter: list[str] | None = None,
        cancel_event=None,
        on_repo_done: Callable[[str, dict, RepoConfig], None] | None = None,
    ) -> dict:
        self.db.mark_wave_run(wave.wave_id, "started", dry_run)
        result = {"completed": 0, "failed": 0, "repo_statuses": {}}
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
        self.db.mark_wave_run(wave.wave_id,
                              "completed" if result["failed"] == 0 else "partial")
        return result
