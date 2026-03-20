"""Scope-targeted rollback — undo specific migration scopes, not just entire waves."""
from __future__ import annotations

import time
from typing import Any

from ado2gh.clients import GHClient
from ado2gh.logging_config import console, log
from ado2gh.models import MigrationScope, MigrationStatus, RepoConfig, WaveConfig
from ado2gh.state.db import StateDB


class RollbackHandler:
    """Rollback migration artifacts with scope-level granularity.

    Supports:
    - Full wave rollback (delete repos + reset all records)
    - Scope-targeted rollback (e.g., only rollback branch_policies for a repo)
    - Repo-level rollback (all scopes for specific repos)
    """

    def __init__(self, gh: GHClient, db: StateDB):
        self.gh = gh
        self.db = db

    def rollback_wave(self, wave: WaveConfig, dry_run: bool = False,
                      scopes: list[str] = None) -> dict:
        """Roll back a wave, optionally limited to specific scopes.

        Args:
            wave: Wave to roll back.
            dry_run: Simulate only.
            scopes: If set, only rollback these scopes (e.g., ["branch_policies", "pipelines"]).
                    If None, rollback everything including repo deletion.
        """
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
                            self._rollback_repo(gh_org, gh_repo, dry_run, stats)
                    elif scope == MigrationScope.BRANCH_POLICIES.value:
                        self._rollback_branch_protection(gh_org, gh_repo, dry_run, stats)
                    elif scope == MigrationScope.PIPELINES.value:
                        self._rollback_pipelines(
                            wave.wave_id, record, dry_run, stats)
                    else:
                        log.info("scope %s rollback for %s/%s: informational only",
                                 scope, gh_org, gh_repo)

                    # Mark as rolled back in DB
                    repo_cfg = RepoConfig(
                        ado_project=record["ado_project"],
                        ado_repo=record["ado_repo"],
                        gh_org=gh_org, gh_repo=gh_repo,
                    )
                    if not dry_run:
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
        if not dry_run:
            self.db.mark_wave_run(wave.wave_id, "rolled_back")

        log.info("Rollback wave %d done: %d scopes, %d repos deleted, "
                 "%d errors in %.1fs",
                 wave.wave_id, stats["scopes_rolled_back"],
                 stats["repos_deleted"], stats["errors"], elapsed)
        return stats

    def rollback_repos(self, repos: list[RepoConfig], wave_id: int,
                       scopes: list[str] = None,
                       dry_run: bool = False) -> dict:
        """Rollback specific repos (not entire wave)."""
        stats = {"repos_deleted": 0, "scopes_rolled_back": 0, "errors": 0}
        target_scopes = scopes or [s.value for s in MigrationScope]

        for repo in repos:
            for scope in target_scopes:
                try:
                    if scope == MigrationScope.REPO.value:
                        self._rollback_repo(
                            repo.gh_org, repo.gh_repo, dry_run, stats)
                    elif scope == MigrationScope.BRANCH_POLICIES.value:
                        self._rollback_branch_protection(
                            repo.gh_org, repo.gh_repo, dry_run, stats)

                    if not dry_run:
                        self.db.upsert_migration(
                            wave_id, repo, scope, MigrationStatus.ROLLED_BACK)
                    stats["scopes_rolled_back"] += 1
                except Exception as exc:
                    log.error("rollback %s/%s scope=%s: %s",
                              repo.gh_org, repo.gh_repo, scope, exc)
                    stats["errors"] += 1

        return stats

    def _rollback_repo(self, gh_org: str, gh_repo: str,
                       dry_run: bool, stats: dict):
        if dry_run:
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
                                     dry_run: bool, stats: dict):
        """Remove branch protection rules from GH repo."""
        if dry_run:
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
                            dry_run: bool, stats: dict):
        """Reset pipeline migration records."""
        if not dry_run:
            self.db.reset_failed_pipeline_migrations(wave_id)
        log.info("Pipeline records reset for wave %d / %s",
                 wave_id, record.get("ado_repo", ""))
