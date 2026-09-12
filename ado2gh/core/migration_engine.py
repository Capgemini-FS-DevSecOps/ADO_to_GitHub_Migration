"""Per-repo migration engine — thin scope dispatcher."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ado2gh.core.concurrency import ConcurrencyManager
from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.scopes.registry import SCOPE_REGISTRY
from ado2gh.logging_config import log
from ado2gh.models import (
    DEFAULT_MIGRATION_STRATEGY,
    ExecutionMode,
    MigrationScope,
    MigrationStatus,
    RepoConfig,
)
from ado2gh.state.base import StateDBBase

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID

    from ado2gh.clients import ADOClient, GHClient


class MigrationEngine:
    """Orchestrates per-repo migration across all requested scopes."""

    SCOPES = [s.value for s in MigrationScope]

    def __init__(  # noqa: PLR0913 - exception-register.md: no existing model groups these
        self,
        global_cfg: dict,
        ado: ADOClient,
        gh: GHClient,
        db: StateDBBase,
        mode: ExecutionMode = ExecutionMode.LIVE,
        concurrency: ConcurrencyManager | None = None,
        assignment_id: str | None = None,
        allowed_repo_keys: set[str] | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        """Wire the engine to its clients, its state store and its execution mode.

        Args:
            global_cfg: Parsed `global` block of migration.yaml.
            ado: Azure DevOps client used to read the source repositories.
            gh: GitHub client used to create and populate the targets.
            db: State store recording per-scope migration rows.
            mode: `ExecutionMode.DRY_RUN` previews without writing anything;
                `ExecutionMode.LIVE` performs the migration (CA-001).
            concurrency: Shared slot manager; built from `global_cfg` when omitted.
            assignment_id: Cohort assignment this run belongs to, if any.
            allowed_repo_keys: When set, only these `project/repo` keys may be
                mutated — anything else is refused as a cross-cohort mutation.
            pipeline_run_id: Accelerator run that owns this engine, used by the
                FR-036 concurrency guard to recognise its own in-progress rows.
        """
        self.cfg = global_cfg
        self.ado = ado
        self.gh = gh
        self.db = db
        self.mode = mode
        self.strategy = global_cfg.get("migration_strategy", DEFAULT_MIGRATION_STRATEGY)
        self.concurrency = concurrency or ConcurrencyManager.from_dict(global_cfg)
        self.assignment_id = assignment_id
        self.allowed_repo_keys = allowed_repo_keys
        self.pipeline_run_id = pipeline_run_id

    def _get_in_progress_block_reason(self, repo: RepoConfig) -> str | None:
        """None when the stale in_progress row was cleared, else why it was kept.

        The reason distinguishes "another run holds this repo" from "conflict
        detection failed", so a refusal is not silently attributed to the wrong
        cause (FR-036).

        Args:
            repo: Repository carrying the in_progress row.

        Returns:
            None when the row was cleared and the migration may proceed, else a
            sentence explaining why it was kept.
        """
        from ado2gh.core.conflict_detection import (
            clear_stale_in_progress_migrations,
            get_repo_conflict_reason,
        )

        reason = get_repo_conflict_reason(
            f"{repo.ado_project}/{repo.ado_repo}", self.pipeline_run_id
        )
        if reason:
            return reason
        cleared = clear_stale_in_progress_migrations(
            self.db,
            repo.ado_project,
            repo.ado_repo,
            current_run_id=self.pipeline_run_id,
        )
        if cleared > 0:
            return None
        return "an in_progress migration row is recorded and could not be cleared"

    def migrate_repo(
        self,
        wave_id: int,
        repo: RepoConfig,
        progress: Progress | None = None,
        task_id: TaskID | None = None,
        pipeline_parallel: int = 8,
    ) -> dict:
        """Migrate one repository across every scope it requests.

        Scope rows are written, never read back as a precondition: a scope
        already recorded as `MigrationStatus.COMPLETED` is dispatched again on
        the next call, not skipped. Whether that is safe is each handler's own
        business — `PipelinesScopeHandler` de-duplicates against the state DB,
        `GitScopeHandler` re-force-pushes under the mirror strategy, and
        `WorkItemsScopeHandler` duplicates every issue. The one precondition
        checked here is the FR-036 in-progress guard, which refuses a repo
        another live run is holding.

        Args:
            wave_id: Wave the migration rows are recorded against.
            repo: Repository to migrate, carrying its own scope list.
            progress: Optional rich progress bar to describe and advance.
            task_id: Task within `progress` belonging to this repository.
            pipeline_parallel: Worker count for the pipelines scope.

        Returns:
            `{"status": ..., "scopes": {...}, "errors": [...]}` where `status` is
            `completed`, `partial` or `failed`.
        """
        repo_key = f"{repo.ado_project}/{repo.ado_repo}"
        if self.allowed_repo_keys and repo_key not in self.allowed_repo_keys:
            return {
                "status": "failed",
                "scopes": {},
                "errors": ["cross-cohort mutation blocked"],
            }
        if self.mode is ExecutionMode.LIVE and self.db.has_repo_in_progress(
            repo.ado_project, repo.ado_repo
        ):
            block_reason = self._get_in_progress_block_reason(repo)
            if block_reason:
                return {
                    "status": "failed",
                    "scopes": {},
                    "errors": [
                        f"repo already has active live migration (FR-036): {block_reason}"
                    ],
                }
        results: dict[str, dict] = {}
        requested = repo.scopes or self.SCOPES
        ctx = ScopeContext(
            global_cfg=self.cfg,
            ado=self.ado,
            gh=self.gh,
            db=self.db,
            mode=self.mode,
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

            if self.mode is ExecutionMode.LIVE:
                self.db.upsert_migration(wave_id, repo, scope, MigrationStatus.IN_PROGRESS)

            if progress and task_id is not None:
                progress.update(
                    task_id, description=f"[cyan]{repo.ado_repo}[/] -> {scope}",
                )

            try:
                kwargs: dict[str, object] = {"concurrency": self.concurrency}
                if scope == MigrationScope.PIPELINES.value:
                    kwargs["pipeline_parallel"] = pipeline_parallel
                    kwargs["wave_id"] = wave_id

                scope_result = handler.migrate(repo, ctx, **kwargs)
                stats = scope_result.stats
                inner_failed = scope_result.failed > 0 or int(stats.get("failed", 0)) > 0

                if inner_failed:
                    results[scope] = {"status": "failed", "detail": stats}
                    if self.mode is ExecutionMode.LIVE:
                        self.db.upsert_migration(
                            wave_id, repo, scope, MigrationStatus.FAILED,
                            stats=stats,
                            error=f"{stats.get('failed', 0)}/{stats.get('total', '?')} item(s) failed",
                        )
                else:
                    results[scope] = {"status": "completed", "detail": stats}
                    if self.mode is ExecutionMode.LIVE:
                        self.db.upsert_migration(
                            wave_id, repo, scope, MigrationStatus.COMPLETED, stats=stats,
                        )
            except Exception as exc:
                log.error(
                    "scope %s failed for %s/%s: %s",
                    scope, repo.ado_project, repo.ado_repo, exc,
                )
                results[scope] = {"status": "failed", "error": str(exc)}
                if self.mode is ExecutionMode.LIVE:
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
