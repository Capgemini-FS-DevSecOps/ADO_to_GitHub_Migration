"""Scope-targeted rollback — undo specific migration scopes, not just entire waves."""
from __future__ import annotations

import time

from ado2gh.clients import GHClient
from ado2gh.clients.ado_client import ADOClient
from ado2gh.core.ado_cleanup import ADOCleanup
from ado2gh.logging_config import log
from ado2gh.models import (
    ExecutionMode,
    MigrationScope,
    MigrationStatus,
    RepoConfig,
    WaveConfig,
)
from ado2gh.state.base import StateDBBase


class RollbackHandler:
    """Roll back migration artifacts with scope-level granularity.

    Supports:
    - Full wave rollback (delete repos + reset all records)
    - Scope-targeted rollback (e.g., only rollback branch_policies for a repo)
    """

    def __init__(self, gh: GHClient, db: StateDBBase, ado: ADOClient | None = None) -> None:
        """Wire the handler to the clients and state store it rolls back with.

        Args:
            gh: GitHub client used to delete repos and remove protection rules.
            db: State store holding the migration rows being reset.
            ado: Azure DevOps client, needed only to re-enable ADO pipelines
                (FR-026a); pipeline re-enablement is skipped when it is None.
        """
        self.gh = gh
        self.db = db
        self.ado = ado

    def rollback_wave(self, wave: WaveConfig,
                      mode: ExecutionMode = ExecutionMode.LIVE,
                      scopes: list[str] | None = None) -> dict:
        """Roll back a wave, optionally limited to specific scopes.

        Args:
            wave: Wave to roll back.
            mode: `ExecutionMode.DRY_RUN` logs what would happen and touches
                nothing; `ExecutionMode.LIVE` performs the rollback (CA-001).
            scopes: If set, only rollback these scopes (e.g., ["branch_policies", "pipelines"]).
                    If None, rollback everything including repo deletion.

        Returns:
            Counters for repos deleted, scopes rolled back and errors seen.
        """
        dry_run = mode is ExecutionMode.DRY_RUN
        log.info("Rollback wave %d: %s (scopes=%s)%s",
                 wave.wave_id, wave.name, scopes or "ALL",
                 " [DRY RUN]" if dry_run else "")

        start = time.monotonic()
        stats = {"repos_deleted": 0, "scopes_rolled_back": 0, "errors": 0}

        migrations = self.db.get_wave_migrations(wave.wave_id)
        completed = [m for m in migrations
                     if m["status"] == MigrationStatus.COMPLETED.value]

        if scopes:
            completed = [m for m in completed if m["scope"] in scopes]

        # Group by repo
        by_repo: dict[str, list[dict]] = {}
        for m in completed:
            key = f"{m['gh_org']}/{m['gh_repo']}"
            by_repo.setdefault(key, []).append(m)

        for repo_key, records in by_repo.items():
            for record in records:
                scope = record["scope"]
                gh_org = record["gh_org"]
                gh_repo = record["gh_repo"]

                try:
                    if scope == MigrationScope.REPO.value:
                        if scopes is None or MigrationScope.REPO.value in scopes:
                            self._rollback_repo(gh_org, gh_repo, mode, stats)
                    elif scope == MigrationScope.BRANCH_POLICIES.value:
                        self._rollback_branch_protection(gh_org, gh_repo, mode)
                    elif scope == MigrationScope.PIPELINES.value:
                        self._rollback_pipelines(
                            wave.wave_id, record, mode, stats)
                    else:
                        log.info("scope %s rollback for %s/%s: informational only",
                                 scope, gh_org, gh_repo)

                    # Mark as rolled back in DB
                    repo_cfg = RepoConfig(
                        ado_project=record["ado_project"],
                        ado_repo=record["ado_repo"],
                        gh_org=gh_org, gh_repo=gh_repo,
                    )
                    if mode is ExecutionMode.LIVE:
                        self.db.upsert_migration(
                            wave.wave_id, repo_cfg, scope,
                            MigrationStatus.ROLLED_BACK,
                        )
                    stats["scopes_rolled_back"] += 1

                except Exception as exc:
                    log.error("rollback failed %s/%s scope=%s: %s",
                              gh_org, gh_repo, scope, exc)
                    stats["errors"] += 1

        elapsed = round(time.monotonic() - start, 2)
        if mode is ExecutionMode.LIVE:
            self.db.mark_wave_run(wave.wave_id, "rolled_back")

        log.info("Rollback wave %d done: %d scopes, %d repos deleted, "
                 "%d errors in %.1fs",
                 wave.wave_id, stats["scopes_rolled_back"],
                 stats["repos_deleted"], stats["errors"], elapsed)
        return stats

    def _rollback_repo(self, gh_org: str, gh_repo: str,
                       mode: ExecutionMode, stats: dict) -> None:
        """Delete the migrated GitHub repository.

        Args:
            gh_org: GitHub organisation holding the repository.
            gh_repo: Repository name to delete.
            mode: `ExecutionMode.DRY_RUN` only logs the intent.
            stats: Counter dict the deletion is recorded in.

        Raises:
            RuntimeError: GitHub reported the deletion as failed.
        """
        if mode is ExecutionMode.DRY_RUN:
            log.info("[DRY RUN] would delete %s/%s", gh_org, gh_repo)
            stats["repos_deleted"] += 1
            return

        if self.gh.repo_exists(gh_org, gh_repo):
            if self.gh.delete_repo(gh_org, gh_repo):
                log.info("Deleted %s/%s", gh_org, gh_repo)
                stats["repos_deleted"] += 1
            else:
                raise RuntimeError(f"Failed to delete {gh_org}/{gh_repo}")

    def _rollback_branch_protection(self, gh_org: str, gh_repo: str,
                                    mode: ExecutionMode) -> None:
        """Remove branch protection rules from the GitHub repo.

        Args:
            gh_org: GitHub organisation holding the repository.
            gh_repo: Repository whose default-branch protection is removed.
            mode: `ExecutionMode.DRY_RUN` only logs the intent.
        """
        if mode is ExecutionMode.DRY_RUN:
            log.info("[DRY RUN] would remove branch protection from %s/%s",
                     gh_org, gh_repo)
            return

        try:
            gh_repo_info = self.gh.get_repo(gh_org, gh_repo)
            default_branch = gh_repo_info.get("default_branch", "main")
            r = self.gh._delete(
                f"/repos/{gh_org}/{gh_repo}/branches/{default_branch}/protection"
            )
            if r.ok:
                log.info("Removed branch protection from %s/%s:%s",
                         gh_org, gh_repo, default_branch)
        except Exception as exc:
            log.warning("branch protection removal failed: %s", exc)

    def _rollback_pipelines(self, wave_id: int, record: dict,
                            mode: ExecutionMode, stats: dict) -> None:
        """Reset pipeline migration records; re-enable ADO pipelines when configured (FR-026a).

        Args:
            wave_id: Wave whose failed pipeline migrations are reset.
            record: Migration row identifying the repository.
            mode: `ExecutionMode.DRY_RUN` leaves the state store untouched and
                asks the cleanup helper to preview only.
            stats: Counter dict the re-enabled pipeline count is recorded in.
        """
        if mode is ExecutionMode.LIVE:
            self.db.reset_failed_pipeline_migrations(wave_id)
        if self.ado and MigrationScope.PIPELINES.value in (
            record.get("scope", MigrationScope.PIPELINES.value),
        ):
            repo = RepoConfig(
                ado_project=record["ado_project"],
                ado_repo=record["ado_repo"],
                gh_org=record["gh_org"],
                gh_repo=record["gh_repo"],
            )
            cleanup = ADOCleanup(self.ado, mode=mode)
            enable_stats = cleanup.enable_pipelines(repo)
            stats["ado_pipelines_reenabled"] = enable_stats.get("enabled", 0)
        log.info("Pipeline rollback for wave %d / %s",
                 wave_id, record.get("ado_repo", ""))
