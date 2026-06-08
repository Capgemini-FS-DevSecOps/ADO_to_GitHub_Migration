"""Per-repo migration engine — thin scope dispatcher."""
from __future__ import annotations

from typing import Any

from ado2gh.clients import ADOClient, GHClient
from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.scopes.registry import SCOPE_REGISTRY
from ado2gh.infra.concurrency import ConcurrencyManager
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, MigrationStatus, RepoConfig
from ado2gh.state.db import StateDB


class MigrationEngine:
    """Orchestrates per-repo migration across all requested scopes."""

    SCOPES = [s.value for s in MigrationScope]

    def __init__(
        self,
        global_cfg: dict,
        ado: ADOClient,
        gh: GHClient,
        db: StateDB,
        dry_run: bool = False,
        concurrency: ConcurrencyManager | None = None,
    ):
        self.cfg = global_cfg
        self.ado = ado
        self.gh = gh
        self.db = db
        self.dry_run = dry_run
        self.strategy = global_cfg.get("migration_strategy", "mirror")
        self.concurrency = concurrency or ConcurrencyManager.from_dict(global_cfg)

    def migrate_repo(
        self,
        wave_id: int,
        repo: RepoConfig,
        progress: Any = None,
        task_id: Any = None,
        pipeline_parallel: int = 8,
    ) -> dict:
        results: dict[str, dict] = {}
        requested = repo.scopes or self.SCOPES
        ctx = ScopeContext(
            global_cfg=self.cfg,
            ado=self.ado,
            gh=self.gh,
            db=self.db,
            dry_run=self.dry_run,
            strategy=self.strategy,
            wave_id=wave_id,
            pipeline_parallel=pipeline_parallel,
        )

        for scope in self.SCOPES:
            if scope not in requested:
                continue
            handler = SCOPE_REGISTRY.get(scope)
            if handler is None:
                continue

            self.db.upsert_migration(wave_id, repo, scope, MigrationStatus.IN_PROGRESS)

            if progress and task_id is not None:
                progress.update(
                    task_id, description=f"[cyan]{repo.ado_repo}[/] -> {scope}",
                )

            try:
                kwargs: dict[str, Any] = {"concurrency": self.concurrency}
                if scope == MigrationScope.PIPELINES.value:
                    kwargs["pipeline_parallel"] = pipeline_parallel
                    kwargs["wave_id"] = wave_id

                scope_result = handler.migrate(repo, ctx, **kwargs)
                stats = scope_result.stats
                inner_failed = scope_result.failed > 0 or int(stats.get("failed", 0)) > 0

                if inner_failed:
                    results[scope] = {"status": "failed", "detail": stats}
                    self.db.upsert_migration(
                        wave_id, repo, scope, MigrationStatus.FAILED,
                        stats=stats,
                        error=f"{stats.get('failed', 0)}/{stats.get('total', '?')} item(s) failed",
                    )
                else:
                    results[scope] = {"status": "completed", "detail": stats}
                    self.db.upsert_migration(
                        wave_id, repo, scope, MigrationStatus.COMPLETED, stats=stats,
                    )
            except Exception as exc:
                log.error(
                    "scope %s failed for %s/%s: %s",
                    scope, repo.ado_project, repo.ado_repo, exc,
                )
                results[scope] = {"status": "failed", "error": str(exc)}
                self.db.upsert_migration(
                    wave_id, repo, scope, MigrationStatus.FAILED, error=str(exc),
                )

        completed = sum(1 for v in results.values() if v["status"] == "completed")
        total = len(results)
        overall = (
            "completed" if completed == total
            else ("partial" if completed > 0 else "failed")
        )

        if progress and task_id is not None:
            progress.advance(task_id)

        return {
            "status": overall,
            "scopes": results,
            "errors": [v["error"] for v in results.values() if v.get("error")],
        }
