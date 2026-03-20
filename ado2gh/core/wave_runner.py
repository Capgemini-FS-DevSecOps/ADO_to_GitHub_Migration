"""Wave-level orchestrator — runs all repos in a wave with parallel workers."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from ado2gh.clients import ADOClient, GHClient
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.logging_config import console, log
from ado2gh.models import MigrationStatus, WaveConfig
from ado2gh.state.db import StateDB


class WaveRunner:
    """Execute all repository migrations within a single wave.

    Each repo is dispatched to a :class:`MigrationEngine` instance.  Repos
    within the wave run in parallel up to ``wave.parallel`` workers.
    A rich progress bar provides real-time feedback.
    """

    def __init__(
        self,
        global_cfg: dict,
        ado: ADOClient,
        gh: GHClient,
        db: StateDB,
    ):
        self.cfg = global_cfg
        self.ado = ado
        self.gh = gh
        self.db = db

    # ── Public API ───────────────────────────────────────────────────────────

    def run_wave(self, wave: WaveConfig, dry_run: bool = False) -> dict:
        """Execute *wave* and return an aggregate summary.
        -------
        dict
            ``{"wave_id": ..., "status": ..., "repos": {...}, "elapsed_sec": ...}``
        """
        log.info(
            "=== Starting wave %d: %s (%d repos, parallel=%d)%s ===",
            wave.wave_id, wave.name, len(wave.repos),
            wave.parallel, " [DRY RUN]" if dry_run else "",
        )

        run_id = self.db.mark_wave_run(wave.wave_id, "started", dry_run=dry_run)
        engine = MigrationEngine(self.cfg, self.ado, self.gh, self.db, dry_run=dry_run)

        start = time.monotonic()
        repo_results: dict[str, dict] = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            wave_task = progress.add_task(
                f"Wave {wave.wave_id}: {wave.name}",
                total=len(wave.repos),
            )

            with ThreadPoolExecutor(max_workers=wave.parallel) as pool:
                futures = {}
                for repo in wave.repos:
                    repo_task = progress.add_task(
                        f"[cyan]{repo.ado_repo}[/] pending", total=None,
                    )
                    pipeline_par = repo.pipeline_parallel or wave.pipeline_parallel
                    fut = pool.submit(
                        self._run_single_repo,
                        engine, wave.wave_id, repo,
                        progress, repo_task, pipeline_par,
                    )
                    futures[fut] = (repo, repo_task)

                for fut in as_completed(futures):
                    repo, repo_task = futures[fut]
                    key = f"{repo.ado_project}/{repo.ado_repo}"
                    try:
                        result = fut.result()
                        repo_results[key] = result
                        status_icon = (
                            "[green]done[/]" if result["status"] == "completed"
                            else "[yellow]partial[/]" if result["status"] == "partial"
                            else "[red]fail[/]"
                        )
                        progress.update(
                            repo_task,
                            description=f"[cyan]{repo.ado_repo}[/] {status_icon}",
                            completed=1, total=1,
                        )
                    except Exception as exc:
                        log.error("repo %s raised: %s", key, exc)
                        repo_results[key] = {"status": "failed", "error": str(exc)}
                        progress.update(
                            repo_task,
                            description=f"[cyan]{repo.ado_repo}[/] [red]error[/]",
                            completed=1, total=1,
                        )

                    progress.advance(wave_task)

        elapsed = time.monotonic() - start

        # Determine overall wave status
        statuses = [r["status"] for r in repo_results.values()]
        if all(s == "completed" for s in statuses):
            overall = "completed"
        elif any(s == "completed" for s in statuses):
            overall = "partial"
        else:
            overall = "failed"

        self.db.mark_wave_run(wave.wave_id, overall)

        summary = {
            "wave_id": wave.wave_id,
            "name": wave.name,
            "status": overall,
            "dry_run": dry_run,
            "repos": repo_results,
            "completed": sum(1 for s in statuses if s == "completed"),
            "failed": sum(1 for s in statuses if s == "failed"),
            "partial": sum(1 for s in statuses if s == "partial"),
            "total": len(statuses),
            "elapsed_sec": round(elapsed, 2),
        }

        log.info(
            "=== Wave %d finished: %s — %d/%d completed in %.1fs ===",
            wave.wave_id, overall, summary["completed"],
            summary["total"], elapsed,
        )

        return summary

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _run_single_repo(
        engine: MigrationEngine,
        wave_id: int,
        repo: Any,
        progress: Any,
        task_id: Any,
        pipeline_parallel: int,
    ) -> dict:
        """Wrapper that calls ``engine.migrate_repo`` — suitable for executor."""
        return engine.migrate_repo(
            wave_id, repo,
            progress=progress,
            task_id=task_id,
            pipeline_parallel=pipeline_parallel,
        )
