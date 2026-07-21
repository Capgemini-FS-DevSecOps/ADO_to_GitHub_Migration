"""Scope-targeted rollback — undo specific migration scopes, not just entire waves."""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from ado2gh.clients import GHClient
from ado2gh.logging_config import console, log
from ado2gh.models import MigrationScope, MigrationStatus, RepoConfig, WaveConfig
from ado2gh.state.db import StateDB


class RollbackHandler:
    """Rollback migration artifacts with scope-level granularity.

    Supports:
    - Full wave rollback (delete repos + reset all records)
    - Scope-targeted rollback only when an exact, ownership-safe inverse exists
    - Repo-level rollback (all scopes for specific repos)

    Branch-policy rollback is deliberately refused until per-policy before and
    after fingerprints are persisted.  Deleting protection for a whole branch
    would also remove operator-owned or subsequently modified settings.
    """

    DESTRUCTIVE_OPERATION_KIND = "github_rollback"
    REPOSITORY_DELETE_DISABLED = (
        "Automatic whole-repository deletion is disabled: GitHub does not "
        "offer one complete, race-free inventory of every downstream use "
        "(packages, projects, discussions, deployments, integrations, and "
        "credentials). Use scope-targeted rollback, quarantine/archive the "
        "target, and perform separately reviewed manual deletion if required."
    )

    def __init__(
        self,
        gh: GHClient,
        db: StateDB,
        *,
        authorized_plan_id: str = "",
        authorized_run_id: str = "",
        destructive_capability_id: str = "",
    ):
        self.gh = gh
        self.db = db
        self.authorized_plan_id = str(authorized_plan_id or "")
        self.authorized_run_id = str(authorized_run_id or "")
        self.destructive_capability_id = str(
            destructive_capability_id or ""
        )
        self._destructive_claimant = f"rollback_{uuid4().hex}"
        self._destructive_claim_token = ""
        self._destructive_request: dict[str, Any] = {}
        self._approved_target_snapshots: dict[str, dict[str, Any]] = {}
        self._approved_source_by_target: dict[str, str] = {}

    @staticmethod
    def _canonical_ref_rows(rows: Any, namespace: str) -> list[list[str]]:
        if not isinstance(rows, list):
            raise TypeError("GitHub matching-refs response must be a list")
        prefix = f"refs/{namespace}/"
        refs: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("object"), dict):
                raise ValueError("GitHub matching-ref entry is malformed")
            name = str(row.get("ref", ""))
            sha = str(row["object"].get("sha", "")).strip().lower()
            if not name.startswith(prefix) or not name[len(prefix):] or not sha:
                raise ValueError("GitHub matching-ref identity is incomplete")
            if name in refs:
                raise ValueError(f"GitHub returned duplicate ref {name!r}")
            refs[name] = sha
        return [[name, refs[name]] for name in sorted(refs)]

    def _capture_target_snapshot(self, gh_org: str, gh_repo: str) -> dict[str, Any]:
        """Capture all target state that can reveal post-approval human use."""
        target = self.gh.get_repo(gh_org, gh_repo)
        if not isinstance(target, dict):
            raise TypeError("GitHub repository response must be an object")
        target_repo_id = self._immutable_repo_id(target)
        if not target_repo_id:
            raise RuntimeError("GitHub repository has no immutable ID")
        list_refs = getattr(self.gh, "list_git_refs", None)
        list_issues = getattr(self.gh, "list_issues", None)
        list_releases = getattr(self.gh, "list_releases", None)
        if not all(callable(item) for item in (list_refs, list_issues, list_releases)):
            raise RuntimeError(
                "Live rollback requires complete refs, issues, and releases APIs"
            )
        issues = list_issues(gh_org, gh_repo, state="all")
        releases = list_releases(gh_org, gh_repo)
        if not isinstance(issues, list) or not isinstance(releases, list):
            raise TypeError("GitHub target artifact inventory is incomplete")

        def artifacts(rows: list[dict], kind: str) -> list[list[str]]:
            result: list[list[str]] = []
            for row in rows:
                if not isinstance(row, dict) or row.get("id") in (None, ""):
                    raise ValueError(f"GitHub {kind} entry is malformed")
                result.append([
                    str(row["id"]),
                    str(row.get("node_id") or ""),
                    str(row.get("updated_at") or row.get("published_at") or ""),
                ])
            return sorted(result)

        return {
            "target_repo_id": target_repo_id,
            "visibility": target.get("visibility"),
            "default_branch": str(target.get("default_branch") or ""),
            "archived": bool(target.get("archived", False)),
            "updated_at": str(target.get("updated_at") or ""),
            "pushed_at": str(target.get("pushed_at") or ""),
            "branch_refs": self._canonical_ref_rows(
                list_refs(gh_org, gh_repo, "heads"), "heads"
            ),
            "tag_refs": self._canonical_ref_rows(
                list_refs(gh_org, gh_repo, "tags"), "tags"
            ),
            # The issues endpoint includes pull requests. IDs/timestamps prove
            # stability without persisting titles, bodies, or other content.
            "issues_and_pulls": artifacts(issues, "issue"),
            "releases": artifacts(releases, "release"),
        }

    def build_capability_request(
        self, wave: WaveConfig, scopes: list[str] = None
    ) -> dict[str, Any]:
        """Build the exact content-addressed request shown for approval."""
        if scopes is None or MigrationScope.REPO.value in scopes:
            raise PermissionError(self.REPOSITORY_DELETE_DISABLED)
        targets = []
        for repo in sorted(
            wave.repos,
            key=lambda item: (item.gh_org.casefold(), item.gh_repo.casefold()),
        ):
            targets.append({
                "source_key": f"{repo.ado_project}/{repo.ado_repo}",
                "target_key": f"{repo.gh_org}/{repo.gh_repo}",
                "snapshot": self._capture_target_snapshot(
                    repo.gh_org, repo.gh_repo
                ),
            })
        return {
            "schema_version": 1,
            "wave_id": int(wave.wave_id),
            "scopes": sorted(set(scopes)) if scopes else ["all"],
            "acknowledge_target_data_loss": scopes is None
                or MigrationScope.REPO.value in scopes,
            "targets": targets,
        }

    def _claim_destructive_capability(
        self, wave: WaveConfig, scopes: list[str] = None
    ) -> None:
        if not (
            self.authorized_plan_id
            and self.authorized_run_id
            and self.destructive_capability_id
        ):
            raise PermissionError(
                "Live rollback requires a plan/run-bound destructive capability"
            )
        request = self.build_capability_request(wave, scopes)
        claim = self.db.claim_pev_destructive_capability(
            self.destructive_capability_id,
            self.authorized_plan_id,
            self.authorized_run_id,
            self.DESTRUCTIVE_OPERATION_KIND,
            request,
            self._destructive_claimant,
        )
        self._destructive_claim_token = claim
        self._destructive_request = request
        self._approved_target_snapshots = {
            str(item["target_key"]).casefold(): dict(item["snapshot"])
            for item in request["targets"]
        }
        self._approved_source_by_target = {
            str(item["target_key"]).casefold(): str(item["source_key"])
            for item in request["targets"]
        }

    def _finish_destructive_capability(self, *, success: bool) -> None:
        if not self._destructive_claim_token:
            return
        self.db.finish_pev_destructive_capability(
            self.destructive_capability_id,
            plan_id=self.authorized_plan_id,
            run_id=self.authorized_run_id,
            operation_kind=self.DESTRUCTIVE_OPERATION_KIND,
            claimant=self._destructive_claimant,
            claim_token=self._destructive_claim_token,
            status="completed" if success else "failed",
            error=None if success else "one or more rollback actions failed",
        )

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

        if not dry_run:
            self._claim_destructive_capability(wave, scopes)

        start = time.monotonic()
        stats = {"repos_deleted": 0, "scopes_rolled_back": 0, "errors": 0}

        migrations = self.db.get_wave_migrations(wave.wave_id)
        # Failed/interrupted scopes can have partial remote side effects.  The
        # exact run-bound ownership receipt, not a success label, is the
        # authority for destructive cleanup after such a failure.
        rollback_candidates = [
            m for m in migrations
            if m["status"] in {
                MigrationStatus.COMPLETED.value,
                MigrationStatus.FAILED.value,
                MigrationStatus.NEEDS_REVIEW.value,
                MigrationStatus.IN_PROGRESS.value,
            }
        ]

        if scopes:
            rollback_candidates = [
                m for m in rollback_candidates if m["scope"] in scopes
            ]

        # Group by repo
        by_repo: dict[str, list[dict]] = {}
        for m in rollback_candidates:
            key = f"{m['gh_org']}/{m['gh_repo']}"
            by_repo.setdefault(key, []).append(m)

        for repo_key, records in by_repo.items():
            repo_deleted = False
            for record in records:
                scope = record["scope"]
                gh_org = record["gh_org"]
                gh_repo = record["gh_repo"]

                try:
                    if repo_deleted and scopes is None:
                        # Deleting an owned repository removes all of its
                        # contained artifacts; do not issue follow-up writes.
                        pass
                    if scope == MigrationScope.REPO.value:
                        if scopes is None or MigrationScope.REPO.value in scopes:
                            self._rollback_repo(gh_org, gh_repo, dry_run, stats)
                            repo_deleted = True
                    elif scope == MigrationScope.BRANCH_POLICIES.value:
                        if repo_deleted:
                            pass
                        else:
                            self._rollback_branch_protection(
                                gh_org, gh_repo, dry_run, stats
                            )
                    elif scope == MigrationScope.PIPELINES.value:
                        if not repo_deleted:
                            self._rollback_pipelines(
                                wave.wave_id, record, dry_run, stats)
                    else:
                        if not repo_deleted:
                            raise NotImplementedError(
                                f"Rollback for scope {scope!r} has no safe inverse"
                            )

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
            self.db.mark_wave_run(
                wave.wave_id,
                "rolled_back" if stats["errors"] == 0 else "rollback_partial",
            )
            self._finish_destructive_capability(
                success=stats["errors"] == 0
            )

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
        if not dry_run:
            self._claim_destructive_capability(
                WaveConfig(
                    wave_id=wave_id,
                    name=f"PEV rollback {self.authorized_plan_id}",
                    description="Exact capability-bound repository rollback",
                    repos=list(repos),
                ),
                scopes,
            )

        for repo in repos:
            for scope in target_scopes:
                try:
                    if scope == MigrationScope.REPO.value:
                        self._rollback_repo(
                            repo.gh_org, repo.gh_repo, dry_run, stats)
                    elif scope == MigrationScope.BRANCH_POLICIES.value:
                        self._rollback_branch_protection(
                            repo.gh_org, repo.gh_repo, dry_run, stats)
                    else:
                        raise NotImplementedError(
                            f"Rollback for scope {scope!r} has no safe inverse"
                        )

                    if not dry_run:
                        self.db.upsert_migration(
                            wave_id, repo, scope, MigrationStatus.ROLLED_BACK)
                    stats["scopes_rolled_back"] += 1
                except Exception as exc:
                    log.error("rollback %s/%s scope=%s: %s",
                              repo.gh_org, repo.gh_repo, scope, exc)
                    stats["errors"] += 1

        if not dry_run:
            self._finish_destructive_capability(
                success=stats["errors"] == 0
            )
        return stats

    def _rollback_repo(self, gh_org: str, gh_repo: str,
                       dry_run: bool, stats: dict):
        if not dry_run:
            raise PermissionError(self.REPOSITORY_DELETE_DISABLED)
        if not dry_run:
            if not self._destructive_claim_token:
                raise PermissionError(
                    "Live repository rollback requires an actively claimed "
                    "destructive capability"
                )
            self.db.assert_pev_destructive_capability(
                self.destructive_capability_id,
                plan_id=self.authorized_plan_id,
                run_id=self.authorized_run_id,
                operation_kind=self.DESTRUCTIVE_OPERATION_KIND,
                claimant=self._destructive_claimant,
                claim_token=self._destructive_claim_token,
                request=self._destructive_request,
            )
        # This first check is read-only and produces precise provenance errors.
        # Live execution repeats it after acquiring the destructive target lock.
        ownership = self._assert_owned_target(
            gh_org, gh_repo, require_authorization=not dry_run
        )
        if dry_run:
            log.info("[DRY RUN] would delete %s/%s", gh_org, gh_repo)
            stats["repos_deleted"] += 1
            return

        lease_owner = ""
        fencing_tokens: dict[str, int] = {}
        if not self.authorized_plan_id or not self.authorized_run_id:
            raise PermissionError(
                "Live repository rollback requires an authorized PEV plan and run"
            )
        lease_owner = f"rollback_{uuid4().hex}"
        acquired = self.db.acquire_pev_target_leases(
            self.authorized_plan_id,
            self.authorized_run_id,
            lease_owner,
            [(gh_org, gh_repo)],
            ttl_seconds=300,
        )
        if acquired is None:
            raise RuntimeError(
                f"Rollback blocked because {gh_org}/{gh_repo} is leased "
                "by an executor or validator"
            )
        fencing_tokens = acquired

        try:
            # Re-read immutable ownership after acquiring the lock to close the
            # check/delete race with an executor, validator, or other rollback.
            ownership = self._assert_owned_target(
                gh_org, gh_repo, require_authorization=True
            )
            expected_snapshot = self._approved_target_snapshots.get(
                f"{gh_org}/{gh_repo}".casefold()
            )
            observed_snapshot = self._capture_target_snapshot(gh_org, gh_repo)
            if expected_snapshot is None or observed_snapshot != expected_snapshot:
                raise PermissionError(
                    f"Target {gh_org}/{gh_repo} changed after destructive "
                    "rollback approval; repository deletion is blocked"
                )
            self.db.assert_pev_target_lease(
                gh_org,
                gh_repo,
                plan_id=self.authorized_plan_id,
                run_id=self.authorized_run_id,
                lease_owner=lease_owner,
                fencing_token=next(iter(fencing_tokens.values())),
            )
            # Refresh immediately before the non-idempotent GraphQL delete.
            # The ownership read above already proved that this exact repo ID
            # exists, so a second retrying name lookup only widens the race.
            if not self.db.renew_pev_target_leases(
                self.authorized_plan_id,
                self.authorized_run_id,
                lease_owner,
                fencing_tokens,
                ttl_seconds=300,
            ):
                raise RuntimeError("Rollback target lease could not be renewed")
            self.db.assert_pev_target_lease(
                gh_org,
                gh_repo,
                plan_id=self.authorized_plan_id,
                run_id=self.authorized_run_id,
                lease_owner=lease_owner,
                fencing_token=next(iter(fencing_tokens.values())),
            )
            delete_by_id = getattr(self.gh, "delete_repo_by_id", None)
            if not callable(delete_by_id):
                raise RuntimeError(
                    "GitHub client does not support immutable-ID repository deletion"
                )
            fencing_token = next(iter(fencing_tokens.values()))
            destructive_receipt = self.db.begin_pev_destructive_action(
                self.destructive_capability_id,
                plan_id=self.authorized_plan_id,
                run_id=self.authorized_run_id,
                operation_kind=self.DESTRUCTIVE_OPERATION_KIND,
                claimant=self._destructive_claimant,
                claim_token=self._destructive_claim_token,
                action_key=(
                    f"delete_repository:{gh_org}/{gh_repo}:"
                    f"{ownership['target_repo_id']}"
                ),
                source_key=self._approved_source_by_target[
                    f"{gh_org}/{gh_repo}".casefold()
                ],
                target_key=f"{gh_org}/{gh_repo}",
                action_kind="delete_repository",
                before=observed_snapshot,
            )
            try:
                delete_operation = self.db.begin_pev_remote_operation(
                    gh_org,
                    gh_repo,
                    plan_id=self.authorized_plan_id,
                    run_id=self.authorized_run_id,
                    lease_owner=lease_owner,
                    fencing_token=fencing_token,
                    operation_kind="delete_repository",
                    operation_payload={
                        "target_repo_id": ownership["target_repo_id"],
                        "target": f"{gh_org}/{gh_repo}",
                    },
                )
            except Exception as exc:
                self.db.finish_pev_destructive_action(
                    destructive_receipt,
                    capability_id=self.destructive_capability_id,
                    claimant=self._destructive_claimant,
                    claim_token=self._destructive_claim_token,
                    status="failed",
                    after={"remote_dispatch_started": False},
                    error=str(exc),
                )
                raise
            try:
                deleted = delete_by_id(ownership["target_repo_id"])
            except Exception:
                self.db.quarantine_pev_target_lease(
                    gh_org,
                    gh_repo,
                    plan_id=self.authorized_plan_id,
                    run_id=self.authorized_run_id,
                    lease_owner=lease_owner,
                    fencing_token=next(iter(fencing_tokens.values())),
                    ttl_seconds=0,
                )
                raise
            if deleted:
                if not self.db.finish_pev_remote_operation(
                    delete_operation,
                    plan_id=self.authorized_plan_id,
                    run_id=self.authorized_run_id,
                    lease_owner=lease_owner,
                    fencing_token=fencing_token,
                    resolution={
                        "deleted": True,
                        "target_repo_id": ownership["target_repo_id"],
                    },
                ):
                    raise RuntimeError(
                        "Repository was deleted but the durable remote-operation "
                        "barrier could not be finalized; manual reconciliation "
                        "is required"
                    )
                if not self.db.finish_pev_destructive_action(
                    destructive_receipt,
                    capability_id=self.destructive_capability_id,
                    claimant=self._destructive_claimant,
                    claim_token=self._destructive_claim_token,
                    status="completed",
                    after={
                        "deleted": True,
                        "target_repo_id": ownership["target_repo_id"],
                    },
                ):
                    raise RuntimeError(
                        "Repository was deleted but its destructive action "
                        "receipt could not be finalized"
                    )
                log.info("Deleted %s/%s", gh_org, gh_repo)
                stats["repos_deleted"] += 1
                if not self.db.mark_repository_rolled_back(
                    gh_org,
                    gh_repo,
                    plan_id=ownership["ownership_plan_id"],
                    run_id=ownership["ownership_run_id"],
                ):
                    raise RuntimeError(
                        f"Deleted {gh_org}/{gh_repo} but could not finalize its "
                        "ownership receipt; manual audit is required"
                    )
            else:
                self.db.quarantine_pev_target_lease(
                    gh_org,
                    gh_repo,
                    plan_id=self.authorized_plan_id,
                    run_id=self.authorized_run_id,
                    lease_owner=lease_owner,
                    fencing_token=next(iter(fencing_tokens.values())),
                    ttl_seconds=0,
                )
                raise RuntimeError(f"Failed to delete {gh_org}/{gh_repo}")
        finally:
            if fencing_tokens:
                self.db.release_pev_target_leases(
                    self.authorized_plan_id,
                    self.authorized_run_id,
                    lease_owner,
                    fencing_tokens,
                )

    def _rollback_branch_protection(self, gh_org: str, gh_repo: str,
                                     dry_run: bool, stats: dict):
        """Refuse branch-wide deletion without exact artifact provenance."""
        del dry_run, stats
        raise NotImplementedError(
            f"Automated branch-policy rollback is disabled for {gh_org}/{gh_repo}: "
            "the migration state does not contain exact agent-owned before/after "
            "fingerprints for each affected branch policy. A branch-wide delete "
            "could remove operator-owned or subsequently modified protection. "
            "Manual remediation is required: review the migration's "
            "branch-policy evidence, compare every affected branch with the "
            "approved source policy snapshot, and restore only verified "
            "agent-created settings."
        )

    def _rollback_pipelines(self, wave_id: int, record: dict,
                            dry_run: bool, stats: dict):
        """Refuse to pretend a database reset removes merged workflows."""
        raise NotImplementedError(
            "Pipeline rollback requires a reviewed reverse PR; resetting local "
            "state would leave target workflows active"
        )

    @staticmethod
    def _immutable_repo_id(value: dict) -> str:
        return str(value.get("node_id") or value.get("id") or "").strip()

    def _assert_owned_target(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        require_authorization: bool = False,
    ) -> dict:
        """Require a run-bound ownership receipt and the same live repo id."""
        mappings = self.db.list_repository_mappings()
        match = next(
            (
                item for item in mappings
                if str(item.get("gh_org", "")).casefold() == gh_org.casefold()
                and str(item.get("gh_repo", "")).casefold() == gh_repo.casefold()
            ),
            None,
        )
        if not match:
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} has no PEV ownership receipt"
            )
        if match.get("status") == "adopted":
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} was adopted, not created by this plan"
            )
        if match.get("status") not in {"created", "migrated"}:
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} has no created-by-run ownership "
                f"receipt (state={match.get('status')!r})"
            )
        plan_id = str(match.get("ownership_plan_id") or "")
        run_id = str(match.get("ownership_run_id") or "")
        recorded_repo_id = str(match.get("target_repo_id") or "")
        if not plan_id or not run_id or not recorded_repo_id:
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} ownership receipt is incomplete"
            )
        if self.db.repository_has_foreign_target_use(
            gh_org,
            gh_repo,
            owner_plan_id=plan_id,
            owner_run_id=run_id,
            target_repo_id=recorded_repo_id,
        ):
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} was reused by a later or different "
                "PEV run. Deleting it would cascade across that run's artifacts; "
                "automated rollback is refused and requires an explicitly "
                "reviewed multi-run remediation."
            )
        if require_authorization and (
            plan_id != self.authorized_plan_id
            or run_id != self.authorized_run_id
        ):
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} was not created by the authorized run"
            )
        live = self.gh.get_repo(gh_org, gh_repo)
        live_repo_id = self._immutable_repo_id(live)
        if not live_repo_id or live_repo_id != recorded_repo_id:
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} immutable repository id does not "
                "match its ownership receipt"
            )
        if not self.db.repository_owned_by_run(
            gh_org,
            gh_repo,
            plan_id=plan_id,
            run_id=run_id,
            target_repo_id=live_repo_id,
        ):
            raise PermissionError(
                f"Target {gh_org}/{gh_repo} ownership provenance is invalid"
            )
        return match
