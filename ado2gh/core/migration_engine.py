"""Per-repo migration engine — git mirror/GEI + scope handlers."""
from __future__ import annotations

import os
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, TypeVar
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from ado2gh.clients import ADOClient, GHClient
from ado2gh.core.workflow_publisher import WorkflowPublisher
from ado2gh.logging_config import log
from ado2gh.models import (
    MigrationScope,
    MigrationStatus,
    PipelineMetadata,
    RepoConfig,
)
from ado2gh.output_dirs import output_base
from ado2gh.pipelines.pev import create_enterprise_pipeline_transformer_from_env
from ado2gh.pipelines.approvals import (
    ManualApprovalRecord,
    credential_requirements_for_mapping,
    github_environment_configuration_digest,
    verify_record_credential_attestations,
    workflow_secret_names,
)
from ado2gh.pipelines.credential_attestation import (
    CredentialAttestationError,
    CredentialAttestationVerifier,
)
from ado2gh.pipelines.pev_types import PipelineValidationError
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pev.source_integrity import (
    BOUND_NON_GIT_SCOPES,
    create_scope_snapshot,
    fetch_scope_payload,
    validate_scope_snapshot,
    verify_scope_payload,
)
from ado2gh.state.db import PipelineInventorySnapshot, StateDB


_MutationResult = TypeVar("_MutationResult")


class MigrationEngine:
    """Orchestrates per-repo migration across all requested scopes."""

    SCOPES = [s.value for s in MigrationScope]
    ADO_MINIMUM_REVIEWERS_POLICY = "fa4e907d-c16b-4a4c-9dfa-4906e5d171dd"

    def __init__(self, global_cfg: dict, ado: ADOClient, gh: GHClient,
                 db: StateDB, dry_run: bool = False,
                 pipeline_snapshots: Optional[Mapping[
                     str, PipelineInventorySnapshot
                 ]] = None):
        self.cfg = global_cfg
        self.ado = ado
        self.gh = gh
        self.db = db
        self.dry_run = dry_run
        # PEV execution supplies content-addressed snapshots.  Copying into a
        # read-only mapping prevents workers (or a concurrent inventory scan)
        # from changing which normalized pipeline metadata is executed.
        self.pipeline_snapshots = MappingProxyType(dict(pipeline_snapshots or {}))
        raw_source_snapshots = global_cfg.get("pev_source_ref_snapshots", {})
        self.source_ref_snapshots = MappingProxyType({
            str(source_key): MappingProxyType({
                **dict(snapshot),
                "source_branch_refs": MappingProxyType(dict(
                    snapshot.get("source_branch_refs", {})
                )),
                "source_tag_refs": MappingProxyType(dict(
                    snapshot.get("source_tag_refs", {})
                )),
            })
            for source_key, snapshot in (
                raw_source_snapshots.items()
                if isinstance(raw_source_snapshots, Mapping) else ()
            )
            if isinstance(snapshot, Mapping)
        })
        raw_non_git_snapshots = global_cfg.get(
            "pev_non_git_source_snapshots", {}
        )
        self.non_git_source_snapshots = MappingProxyType({
            str(source_key): MappingProxyType({
                str(scope): MappingProxyType(
                    validate_scope_snapshot(str(scope), snapshot)
                )
                for scope, snapshot in scope_snapshots.items()
            })
            for source_key, scope_snapshots in (
                raw_non_git_snapshots.items()
                if isinstance(raw_non_git_snapshots, Mapping) else ()
            )
            if isinstance(scope_snapshots, Mapping)
        })
        raw_work_item_payloads = global_cfg.get("pev_work_item_payloads", {})
        self.work_item_payloads = MappingProxyType({
            str(source_key): list(payload)
            for source_key, payload in (
                raw_work_item_payloads.items()
                if isinstance(raw_work_item_payloads, Mapping) else ()
            )
            if isinstance(payload, list)
        })
        raw_target_snapshots = global_cfg.get("pev_target_snapshots", {})
        self.target_snapshots = MappingProxyType({
            str(source_key): MappingProxyType({
                **dict(snapshot),
                "target_branch_refs": MappingProxyType(dict(
                    snapshot.get("target_branch_refs", {})
                )),
                "target_tag_refs": MappingProxyType(dict(
                    snapshot.get("target_tag_refs", {})
                )),
            })
            for source_key, snapshot in (
                raw_target_snapshots.items()
                if isinstance(raw_target_snapshots, Mapping) else ()
            )
            if isinstance(snapshot, Mapping)
        })
        self._target_fencing_required = bool(
            global_cfg.get("pev_target_fencing_required", False)
        )
        raw_fencing_tokens = global_cfg.get("pev_target_fencing_tokens", {})
        self._target_fencing_tokens = MappingProxyType(
            dict(raw_fencing_tokens)
            if isinstance(raw_fencing_tokens, Mapping) else {}
        )
        self._target_lease_owner = str(
            global_cfg.get("pev_target_lease_owner", "")
        ).strip()
        self._target_identity_lock = threading.Lock()
        self._target_initially_verified: set[str] = set()
        self._target_seen_ids: dict[str, str] = {}
        self._target_use_recorded: set[str] = set()
        # Once a dispatched remote write has an uncertain outcome, this
        # process must not attempt another write to the same target.  The
        # durable in-flight operation also prevents a different process from
        # taking the target lease until an operator reconciles the outcome.
        self._blocked_remote_mutation_targets: set[str] = set()
        raw_scope_digests = global_cfg.get("pev_scope_input_digests", {})
        self._scope_input_digests = MappingProxyType({
            str(source_key): MappingProxyType({
                str(scope): str(digest)
                for scope, digest in scope_digests.items()
            })
            for source_key, scope_digests in (
                raw_scope_digests.items()
                if isinstance(raw_scope_digests, Mapping) else ()
            )
            if isinstance(scope_digests, Mapping)
        })
        # One converter (and therefore one HTTP session) per worker thread.
        # Tests/integrators may still assign ``transformer`` explicitly.
        self.transformer = None
        self._pipeline_local = threading.local()
        self._credential_attestation_verifier = None
        # Migration strategy: "mirror" (default) or "gei"
        self.strategy = global_cfg.get("migration_strategy", "mirror")

    def _get_credential_attestation_verifier(
        self,
    ) -> Optional[CredentialAttestationVerifier]:
        """Load enterprise HMAC keys only when a credential gate needs them."""
        configured = self.cfg.get("pipeline_conversion", {})
        configured = (
            configured.get("credential_attestation")
            if isinstance(configured, Mapping) else None
        )
        if configured is None:
            return None
        if self._credential_attestation_verifier is None:
            self._credential_attestation_verifier = (
                CredentialAttestationVerifier.from_config(configured)
            )
        return self._credential_attestation_verifier

    def migrate_repo(self, wave_id: int, repo: RepoConfig,
                     progress: Any = None, task_id: Any = None,
                     pipeline_parallel: int = 8) -> dict:
        if not self.dry_run and (
            not self._target_fencing_required
            or not self._pev_plan_id()
            or not self._pev_run_id()
        ):
            raise PermissionError(
                "Live MigrationEngine use requires an exact approved PEV plan/run "
                "and active target fencing; use agent plan/execute instead"
            )
        results: dict[str, dict] = {}
        requested = list(repo.scopes or [])
        unknown_scopes = set(requested) - set(self.SCOPES)
        if unknown_scopes:
            raise ValueError(
                f"Unsupported scopes for {repo.ado_project}/{repo.ado_repo}: "
                f"{sorted(unknown_scopes)}"
            )
        if not requested:
            raise ValueError(
                f"No migration scopes requested for {repo.ado_project}/{repo.ado_repo}"
            )
        if not self.dry_run:
            self._assert_target_write_boundary(repo)
        if not self.dry_run:
            if hasattr(self.db, "register_migration_expectations"):
                self.db.register_migration_expectations(wave_id, repo, requested)
            if self._is_pev_execution():
                self.db.register_pev_scope_expectations(
                    wave_id,
                    repo,
                    requested,
                    plan_id=self._pev_plan_id(),
                    run_id=self._pev_run_id(),
                    input_digests={
                        scope: self._scope_input_digest(repo, scope)
                        for scope in requested
                    },
                )

        completed_scopes: set[str] = set()
        if not self.dry_run:
            if self._is_pev_execution():
                source_key = f"{repo.ado_project}/{repo.ado_repo}"
                completed_scopes = {
                    row["scope"]
                    for row in self.db.get_pev_scope_receipts(
                        self._pev_plan_id(),
                        self._pev_run_id(),
                        source_key=source_key,
                    )
                    if row["target_key"] == f"{repo.gh_org}/{repo.gh_repo}"
                    and row["input_digest"]
                    == self._scope_input_digest(repo, row["scope"])
                    and row["status"] == MigrationStatus.COMPLETED.value
                }
            else:
                completed_scopes = {
                    row["scope"]
                    for row in self.db.get_wave_migrations(wave_id)
                    if row["ado_project"] == repo.ado_project
                    and row["ado_repo"] == repo.ado_repo
                    and row["gh_org"] == repo.gh_org
                    and row["gh_repo"] == repo.gh_repo
                    and row["status"] == MigrationStatus.COMPLETED.value
                }

        scope_handlers = {
            MigrationScope.REPO.value: self._migrate_git,
            MigrationScope.WORK_ITEMS.value: self._migrate_work_items,
            MigrationScope.PIPELINES.value: self._migrate_pipelines,
            MigrationScope.WIKI.value: self._migrate_wiki,
            MigrationScope.SECRETS.value: self._migrate_secrets,
            MigrationScope.BRANCH_POLICIES.value: self._migrate_branch_policies,
        }

        for scope in self.SCOPES:
            if scope not in requested:
                continue
            handler = scope_handlers.get(scope)
            if handler is None:
                continue

            if (
                self.cfg.get("enforce_scope_dependencies", False)
                and scope != MigrationScope.REPO.value
                and MigrationScope.REPO.value in requested
                and results.get(MigrationScope.REPO.value, {}).get("status")
                != "completed"
            ):
                error = "blocked because the repository-content scope did not complete"
                results[scope] = {"status": "failed", "error": error,
                                  "detail": {"blocked": True}}
                if not self.dry_run:
                    self._assert_target_fence(repo)
                    self._record_scope_status(
                        wave_id, repo, scope, MigrationStatus.FAILED, error=error
                    )
                continue

            if scope in completed_scopes:
                results[scope] = {
                    "status": "completed",
                    "detail": {"resumed": True, "skipped_completed_scope": True},
                }
                continue

            if not self.dry_run:
                self._assert_target_write_boundary(repo)
                self._record_scope_status(
                    wave_id, repo, scope, MigrationStatus.IN_PROGRESS
                )

            if progress and task_id is not None:
                progress.update(task_id, description=f"[cyan]{repo.ado_repo}[/] -> {scope}")

            try:
                kwargs: dict[str, Any] = {}
                if scope == MigrationScope.PIPELINES.value:
                    kwargs["pipeline_parallel"] = pipeline_parallel
                    kwargs["wave_id"] = wave_id
                if scope in BOUND_NON_GIT_SCOPES:
                    payload, source_evidence = self._fetch_verified_scope_payload(
                        repo, scope
                    )
                    kwargs["_source_payload"] = payload
                    kwargs["_source_evidence"] = source_evidence

                if not self.dry_run:
                    self._assert_target_write_boundary(repo)
                scope_result = handler(repo, **kwargs)
                if not self.dry_run:
                    self._assert_target_write_boundary(repo)
                # Honour inner-failure counts so the scope-level row matches
                # the per-item rows (e.g. pipeline_migrations).
                inner_failed = (
                    isinstance(scope_result, dict)
                    and int(scope_result.get("failed", 0)) > 0
                )
                requires_review = (
                    isinstance(scope_result, dict)
                    and (
                        bool(scope_result.get("requires_review", False))
                        or scope_result.get("production_ready") is False
                    )
                )
                if inner_failed:
                    error = (
                        f"{scope_result.get('failed', 0)}/"
                        f"{scope_result.get('total', '?')} item(s) failed"
                    )
                    results[scope] = {
                        "status": "failed", "detail": scope_result, "error": error
                    }
                    if not self.dry_run:
                        self._record_scope_status(
                            wave_id, repo, scope, MigrationStatus.FAILED,
                            stats=scope_result, error=error,
                        )
                elif requires_review:
                    results[scope] = {
                        "status": "needs_review",
                        "detail": scope_result,
                        "error": scope_result.get(
                            "review_reason", "manual review or external setup required"
                        ),
                    }
                    if not self.dry_run:
                        self._record_scope_status(
                            wave_id, repo, scope, MigrationStatus.NEEDS_REVIEW,
                            stats=scope_result,
                            error=results[scope]["error"],
                        )
                else:
                    results[scope] = {"status": "completed", "detail": scope_result}
                    if not self.dry_run:
                        self._record_scope_status(
                            wave_id, repo, scope, MigrationStatus.COMPLETED,
                            stats=scope_result,
                        )
            except Exception as exc:
                log.error("scope %s failed for %s/%s: %s",
                          scope, repo.ado_project, repo.ado_repo, exc)
                results[scope] = {"status": "failed", "error": str(exc)}
                if not self.dry_run:
                    # A stale executor may report locally, but it must not
                    # update durable state after another owner takes over.
                    self._assert_target_fence(repo)
                    self._record_scope_status(
                        wave_id, repo, scope, MigrationStatus.FAILED,
                        error=str(exc),
                    )

        completed = sum(1 for v in results.values() if v["status"] == "completed")
        total = len(results)
        if self.dry_run:
            if any(value["status"] == "failed" for value in results.values()):
                overall = "failed"
            elif any(
                value["status"] == "needs_review" for value in results.values()
            ):
                overall = "partial"
            else:
                overall = "dry_run"
        elif completed == total:
            overall = "completed"
        elif completed > 0 or any(
            value["status"] == "needs_review" for value in results.values()
        ):
            overall = "partial"
        else:
            overall = "failed"

        if progress and task_id is not None:
            progress.advance(task_id)

        return {
            "status": overall,
            "scopes": results,
            "errors": [v["error"] for v in results.values() if v.get("error")],
        }

    def _pev_plan_id(self) -> str:
        return str(self.cfg.get("pev_plan_id", "")).strip()

    def _pev_run_id(self) -> str:
        return str(self.cfg.get("pev_run_id", "")).strip()

    def _is_pev_execution(self) -> bool:
        plan_id = self._pev_plan_id()
        run_id = self._pev_run_id()
        if bool(plan_id) != bool(run_id):
            raise RuntimeError("PEV scope receipt context is incomplete")
        return bool(plan_id)

    def _scope_input_digest(self, repo: RepoConfig, scope: str) -> str:
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        by_scope = self._scope_input_digests.get(source_key)
        digest = str(by_scope.get(scope, "")) if by_scope is not None else ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(
                f"Approved PEV task input digest is missing for "
                f"{source_key}:{scope}"
            )
        return digest

    def _record_scope_status(
        self,
        wave_id: int,
        repo: RepoConfig,
        scope: str,
        status: MigrationStatus,
        *,
        error: str = None,
        stats: dict = None,
    ) -> None:
        """Persist exact PEV authority first, then the legacy report row."""
        if self._is_pev_execution():
            self.db.upsert_pev_scope_receipt(
                wave_id,
                repo,
                scope,
                status,
                plan_id=self._pev_plan_id(),
                run_id=self._pev_run_id(),
                input_digest=self._scope_input_digest(repo, scope),
                stats=stats,
                error=error,
            )
        self.db.upsert_migration(
            wave_id,
            repo,
            scope,
            status,
            error=error,
            stats=stats,
        )

    @staticmethod
    def _target_lease_key(repo: RepoConfig) -> str:
        return "\x1f".join(
            str(part).strip().rstrip("/").casefold()
            for part in (repo.gh_org, repo.gh_repo)
        )

    def _assert_target_fence(self, repo: RepoConfig) -> None:
        """Block a stale executor before any target write or durable commit."""
        if not self._target_fencing_required:
            return
        plan_id = str(self.cfg.get("pev_plan_id", "")).strip()
        run_id = str(self.cfg.get("pev_run_id", "")).strip()
        key = self._target_lease_key(repo)
        if key in self._blocked_remote_mutation_targets:
            raise RuntimeError(
                f"GitHub target {repo.gh_org}/{repo.gh_repo} has an "
                "unreconciled remote mutation; further writes are blocked"
            )
        token = self._target_fencing_tokens.get(key)
        if (
            not plan_id
            or not run_id
            or not self._target_lease_owner
            or isinstance(token, bool)
            or not isinstance(token, int)
            or token < 1
        ):
            raise RuntimeError(
                f"PEV target fencing context is incomplete for "
                f"{repo.gh_org}/{repo.gh_repo}"
            )
        self.db.assert_pev_target_lease(
            repo.gh_org,
            repo.gh_repo,
            plan_id=plan_id,
            run_id=run_id,
            lease_owner=self._target_lease_owner,
            fencing_token=token,
        )

    def _extend_target_fence(self, repo: RepoConfig, ttl_seconds: int) -> None:
        """Reserve a target for the full duration of a blocking remote write."""
        if not self._target_fencing_required:
            return
        self._assert_target_fence(repo)
        key = self._target_lease_key(repo)
        token = self._target_fencing_tokens[key]
        renewed = self.db.renew_pev_target_leases(
            str(self.cfg.get("pev_plan_id", "")),
            str(self.cfg.get("pev_run_id", "")),
            self._target_lease_owner,
            {key: token},
            ttl_seconds=ttl_seconds,
        )
        if not renewed:
            raise RuntimeError(
                f"Could not extend target fence for {repo.gh_org}/{repo.gh_repo}"
            )
        self._assert_target_fence(repo)

    def _quarantine_target_fence(
        self, repo: RepoConfig, ttl_seconds: int
    ) -> None:
        """Prevent takeover after a target-side process has an uncertain exit."""
        if not self._target_fencing_required:
            return
        key = self._target_lease_key(repo)
        token = self._target_fencing_tokens.get(key)
        if not isinstance(token, int):
            return
        quarantined = self.db.quarantine_pev_target_lease(
            repo.gh_org,
            repo.gh_repo,
            plan_id=str(self.cfg.get("pev_plan_id", "")),
            run_id=str(self.cfg.get("pev_run_id", "")),
            lease_owner=self._target_lease_owner,
            fencing_token=token,
            ttl_seconds=ttl_seconds,
        )
        if quarantined:
            log.error(
                "Quarantined target %s/%s after an uncertain remote write; "
                "manual reconciliation is required before the lease expires",
                repo.gh_org,
                repo.gh_repo,
            )

    def _begin_remote_operation(
        self,
        repo: RepoConfig,
        operation_kind: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> str:
        """Install a durable crash-stop barrier before remote dispatch."""
        if not self._target_fencing_required:
            return ""
        self._assert_target_fence(repo)
        key = self._target_lease_key(repo)
        return self.db.begin_pev_remote_operation(
            repo.gh_org,
            repo.gh_repo,
            plan_id=str(self.cfg.get("pev_plan_id", "")),
            run_id=str(self.cfg.get("pev_run_id", "")),
            lease_owner=self._target_lease_owner,
            fencing_token=self._target_fencing_tokens[key],
            operation_kind=operation_kind,
            operation_payload=payload or {},
        )

    def _finish_remote_operation(
        self,
        repo: RepoConfig,
        operation_id: str,
        resolution: Optional[dict[str, Any]] = None,
    ) -> None:
        """Remove a crash-stop barrier after observed terminal success."""
        if not operation_id:
            return
        key = self._target_lease_key(repo)
        if not self.db.finish_pev_remote_operation(
            operation_id,
            plan_id=str(self.cfg.get("pev_plan_id", "")),
            run_id=str(self.cfg.get("pev_run_id", "")),
            lease_owner=self._target_lease_owner,
            fencing_token=self._target_fencing_tokens[key],
            resolution=resolution or {},
        ):
            raise RuntimeError(
                f"Could not finalize remote-operation barrier for "
                f"{repo.gh_org}/{repo.gh_repo}; manual reconciliation is required"
            )

    def _assert_target_write_boundary(self, repo: RepoConfig) -> None:
        """Fence the worker and bind writes to the approved repository ID.

        The first boundary also checks the complete approved target ref
        snapshot. Later boundaries verify immutable identity without expecting
        refs to remain pristine after this run's own Git writes.
        """
        self._assert_target_fence(repo)
        if not self._target_fencing_required:
            return
        key = self._target_lease_key(repo)
        approved = self._approved_target_snapshot(repo)
        with self._target_identity_lock:
            exists = bool(self.gh.repo_exists(repo.gh_org, repo.gh_repo))
            if not exists:
                if approved["target_exists"] or key in self._target_seen_ids:
                    raise RuntimeError(
                        f"Approved GitHub target {repo.gh_org}/{repo.gh_repo} "
                        "disappeared during execution"
                    )
                self._target_initially_verified.add(key)
                return

            target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            if not isinstance(target, Mapping):
                raise RuntimeError("GitHub target metadata is not an object")
            target_id = self._immutable_repo_id(dict(target))
            if not target_id:
                raise RuntimeError("GitHub target immutable ID is missing")
            if target.get("visibility") != approved["target_visibility"]:
                raise RuntimeError(
                    f"GitHub target visibility drift for "
                    f"{repo.gh_org}/{repo.gh_repo}"
                )
            expected_id = str(approved["target_repo_id"])
            if approved["target_exists"]:
                if target_id != expected_id:
                    raise RuntimeError(
                        f"GitHub target identity drift for "
                        f"{repo.gh_org}/{repo.gh_repo}"
                    )
                if key not in self._target_initially_verified:
                    self._verify_github_target_snapshot(repo, target, approved)
            elif not self._target_owned_by_current_run(repo, dict(target)):
                raise RuntimeError(
                    f"GitHub target {repo.gh_org}/{repo.gh_repo} appeared "
                    "outside the approved run"
                )
            prior_id = self._target_seen_ids.get(key)
            if prior_id and prior_id != target_id:
                raise RuntimeError(
                    f"GitHub target {repo.gh_org}/{repo.gh_repo} was replaced "
                    "during execution"
                )
            self._target_seen_ids[key] = target_id
            if key not in self._target_use_recorded:
                self.db.record_repository_target_use(
                    repo.gh_org,
                    repo.gh_repo,
                    plan_id=str(self.cfg.get("pev_plan_id", "")),
                    run_id=str(self.cfg.get("pev_run_id", "")),
                    target_repo_id=target_id,
                    status="executing",
                )
                self._target_use_recorded.add(key)
            self._target_initially_verified.add(key)

    def _dispatch_github_mutation(
        self,
        repo: RepoConfig,
        operation_kind: str,
        operation_payload: Mapping[str, Any],
        operation: Callable[[], _MutationResult],
        *,
        post_success: Optional[Callable[[_MutationResult], Any]] = None,
    ) -> _MutationResult:
        """Fence and durably journal one name-addressed GitHub API write.

        GitHub's REST API does not accept our fencing token.  The safe local
        protocol is therefore: verify the immutable target identity, extend
        and assert the lease, persist an indefinite in-flight barrier, issue
        exactly one remote mutation, then verify the lease and target again
        before resolving that barrier.  If dispatch raises or the lease is
        lost while the request is in flight, the barrier deliberately remains
        unresolved and this engine instance is permanently stopped for that
        target.
        """
        if self.dry_run:
            raise RuntimeError("GitHub mutations are forbidden during dry-run")
        if not callable(operation):
            raise TypeError("GitHub mutation operation must be callable")
        payload = dict(operation_payload)
        key = self._target_lease_key(repo)

        self._assert_target_write_boundary(repo)
        if self._target_fencing_required:
            configured_ttl = self.cfg.get("execution_lease_seconds", 300)
            try:
                lease_ttl = int(configured_ttl)
            except (TypeError, ValueError):
                lease_ttl = 300
            # GitHub API calls use a 30-second request timeout today.  Keep a
            # much larger reservation so slow GHES proxies cannot routinely
            # cross a lease boundary while a write is in flight.
            self._extend_target_fence(
                repo, min(86_400, max(300, lease_ttl))
            )
        operation_id = self._begin_remote_operation(
            repo, operation_kind, payload
        )
        try:
            result = operation()
            if post_success is not None:
                post_success(result)
            # This assertion is intentionally before barrier resolution.  A
            # stale worker that receives a late HTTP success must leave an
            # in-flight reconciliation barrier rather than continue writing.
            self._assert_target_write_boundary(repo)
            self._finish_remote_operation(
                repo,
                operation_id,
                {
                    "operation_kind": operation_kind,
                    "terminal_success_observed": True,
                },
            )
            self._assert_target_fence(repo)
            return result
        except BaseException:
            if operation_id:
                self._blocked_remote_mutation_targets.add(key)
                log.error(
                    "GitHub mutation %s for %s/%s has an uncertain or "
                    "unfenced outcome; durable reconciliation is required",
                    operation_kind,
                    repo.gh_org,
                    repo.gh_repo,
                )
            raise

    def _ensure_github_environment(
        self,
        repo: RepoConfig,
        env_name: str,
        *,
        require_existing: bool = False,
        required_approver_count: int = 0,
        required_check_count: int = 0,
    ) -> str:
        """Create an absent environment without replacing existing controls."""
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (required_approver_count, required_check_count)
        ):
            raise ValueError("environment protection counts must be non-negative")
        require_existing = bool(
            require_existing or required_approver_count or required_check_count
        )
        get_environment = getattr(self.gh, "get_environment", None)
        if not callable(get_environment):
            raise RuntimeError("GitHub client cannot safely inspect environments")
        existing = get_environment(repo.gh_org, repo.gh_repo, env_name)
        if existing is not None:
            if not isinstance(existing, Mapping):
                raise RuntimeError("GitHub environment readback is malformed")
            self._validate_github_environment_protection(
                repo,
                env_name,
                existing,
                required_approver_count=required_approver_count,
                required_check_count=required_check_count,
            )
            log.info(
                "Preserving existing GitHub environment %s on %s/%s",
                env_name,
                repo.gh_org,
                repo.gh_repo,
            )
            return "preserved"
        if require_existing:
            raise RuntimeError(
                f"Source environment {env_name!r} has approvals or deployment "
                "checks, but the GitHub environment is absent. Configure and "
                "approve its target protection before pipeline delivery"
            )

        def _create_if_still_absent() -> dict[str, Any]:
            if get_environment(
                repo.gh_org, repo.gh_repo, env_name
            ) is not None:
                return {"created": False, "appeared_concurrently": True}
            self.gh.create_environment(repo.gh_org, repo.gh_repo, env_name)
            observed = get_environment(repo.gh_org, repo.gh_repo, env_name)
            if not isinstance(observed, Mapping):
                raise RuntimeError(
                    "GitHub environment creation could not be read back"
                )
            observed_name = str(observed.get("name") or env_name)
            if observed_name != env_name:
                raise RuntimeError(
                    "GitHub environment readback identity mismatch"
                )
            return {"created": True, "name": observed_name}

        result = self._dispatch_github_mutation(
            repo,
            "github_create_environment_if_absent",
            {"environment": env_name},
            _create_if_still_absent,
        )
        return "created" if result.get("created") else "appeared_concurrently"

    def _validate_github_environment_protection(
        self,
        repo: RepoConfig,
        env_name: str,
        environment: Mapping[str, Any],
        *,
        required_approver_count: int,
        required_check_count: int,
    ) -> None:
        """Fail closed when target protection is weaker than source evidence."""
        raw_rules = environment.get("protection_rules", [])
        if not isinstance(raw_rules, list) or any(
            not isinstance(rule, Mapping) for rule in raw_rules
        ):
            raise RuntimeError(
                f"GitHub environment {env_name!r} has malformed protection rules"
            )
        if required_approver_count:
            reviewer_counts = [
                len(rule.get("reviewers", []))
                for rule in raw_rules
                if rule.get("type") == "required_reviewers"
                and isinstance(rule.get("reviewers"), list)
            ]
            observed_reviewers = max(reviewer_counts, default=0)
            if observed_reviewers < required_approver_count:
                raise RuntimeError(
                    f"GitHub environment {env_name!r} has "
                    f"{observed_reviewers} required reviewer(s); source requires "
                    f"at least {required_approver_count}"
                )
        if required_check_count:
            list_custom = getattr(
                self.gh,
                "list_environment_deployment_protection_rules",
                None,
            )
            if not callable(list_custom):
                raise RuntimeError(
                    "GitHub client cannot verify custom deployment protection rules"
                )
            custom_rules = list_custom(
                repo.gh_org, repo.gh_repo, env_name
            )
            if not isinstance(custom_rules, list) or any(
                not isinstance(rule, Mapping) for rule in custom_rules
            ):
                raise RuntimeError(
                    "GitHub custom deployment-protection readback is malformed"
                )
            enabled_count = sum(
                1 for rule in custom_rules if rule.get("enabled") is True
            )
            if enabled_count < required_check_count:
                raise RuntimeError(
                    f"GitHub environment {env_name!r} has {enabled_count} "
                    "enabled custom deployment-protection rule(s); source "
                    f"requires at least {required_check_count}"
                )

    def _ensure_github_branch_protection(
        self, repo: RepoConfig, branch: str, reviewers: int
    ) -> str:
        """Create a rule only for an unprotected branch, then read it back."""
        get_protection = getattr(self.gh, "get_branch_protection", None)
        if not callable(get_protection):
            raise RuntimeError(
                "GitHub client cannot safely inspect branch protection"
            )
        if get_protection(repo.gh_org, repo.gh_repo, branch) is not None:
            log.info(
                "Preserving existing branch protection for %s on %s/%s",
                branch,
                repo.gh_org,
                repo.gh_repo,
            )
            return "preserved"

        def _protect_if_still_unprotected() -> dict[str, Any]:
            if get_protection(repo.gh_org, repo.gh_repo, branch) is not None:
                return {"created": False, "appeared_concurrently": True}
            self.gh.set_branch_protection(
                repo.gh_org,
                repo.gh_repo,
                branch,
                required_reviewers=reviewers,
            )
            observed = get_protection(repo.gh_org, repo.gh_repo, branch)
            if not isinstance(observed, Mapping):
                raise RuntimeError(
                    "GitHub branch protection could not be read back"
                )
            review_rule = observed.get("required_pull_request_reviews")
            if not isinstance(review_rule, Mapping) or (
                review_rule.get("required_approving_review_count") != reviewers
            ) or review_rule.get("dismiss_stale_reviews") is not True:
                raise RuntimeError(
                    "GitHub branch-protection readback differs from the "
                    "approved create-only rule"
                )
            return {"created": True}

        result = self._dispatch_github_mutation(
            repo,
            "github_create_branch_protection_if_absent",
            {"branch": branch, "required_reviewers": reviewers},
            _protect_if_still_unprotected,
        )
        return "created" if result.get("created") else "appeared_concurrently"

    def _apply_github_team_access(
        self,
        repo: RepoConfig,
        github_team: str,
        permission: str,
    ) -> str:
        """Apply and read back one exact, plan-bound team permission."""
        if not github_team or permission not in {
            "pull", "triage", "push", "maintain", "admin",
        }:
            raise ValueError("invalid canonical GitHub team access mapping")

        def _grant_and_verify() -> dict[str, str]:
            self.gh.add_team_to_repo(
                repo.gh_org,
                github_team,
                repo.gh_repo,
                permission=permission,
            )
            get_permission = getattr(
                self.gh, "get_team_repo_permission", None
            )
            if not callable(get_permission):
                raise RuntimeError(
                    "GitHub client cannot verify team repository access"
                )
            observed = get_permission(
                repo.gh_org, github_team, repo.gh_repo
            )
            if observed != permission:
                raise RuntimeError(
                    f"GitHub team {github_team!r} permission mismatch: "
                    f"expected {permission!r}, observed {observed!r}"
                )
            return {"permission": observed}

        result = self._dispatch_github_mutation(
            repo,
            "github_add_team_repository_permission",
            {"team_slug": github_team, "permission": permission},
            _grant_and_verify,
        )
        return result["permission"]

    def _fetch_verified_scope_payload(
        self, repo: RepoConfig, scope: str
    ) -> tuple[Any, dict[str, Any]]:
        """Fetch once, verify, then hand the same object to the transformer."""
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        ref_snapshot = self.source_ref_snapshots.get(source_key)
        source_repo_id = (
            str(ref_snapshot.get("source_repo_id", "")).strip()
            if isinstance(ref_snapshot, Mapping) else ""
        )
        if not source_repo_id:
            source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
            if not isinstance(source, Mapping):
                raise RuntimeError("ADO repository response must be an object")
            source_repo_id = str(source.get("id", "")).strip()
        if (
            scope == MigrationScope.WORK_ITEMS.value
            and source_key in self.work_item_payloads
        ):
            # The executor hydrates each ADO project once and indexes this
            # phase-local payload by immutable repository ID.  Reusing it here
            # avoids a full project query for every repository.
            payload = list(self.work_item_payloads[source_key])
        elif scope == MigrationScope.WORK_ITEMS.value and self._is_pev_execution():
            raise RuntimeError(
                f"PEV work-item payload is missing for {source_key}"
            )
        else:
            payload = fetch_scope_payload(
                self.ado,
                repo,
                scope,
                source_repo_id=source_repo_id,
                include_unlinked_work_items=bool(
                    self.cfg.get("include_unlinked_work_items", False)
                ),
            )
        approved_by_scope = self.non_git_source_snapshots.get(source_key)
        approved = (
            approved_by_scope.get(scope)
            if isinstance(approved_by_scope, Mapping) else None
        )
        if self.cfg.get("pev_plan_id") and approved is None:
            raise RuntimeError(
                f"Approved {scope} source snapshot is missing for {source_key}"
            )
        if approved is None:
            return payload, create_scope_snapshot(scope, payload)
        return payload, verify_scope_payload(scope, payload, approved)

    # ── GIT MIGRATION (the actual mirror) ───────────────────────────────────

    @staticmethod
    def _immutable_repo_id(value: dict[str, Any]) -> str:
        return str(value.get("node_id") or value.get("id") or "").strip()

    def _record_target_ownership(
        self,
        repo: RepoConfig,
        target: dict[str, Any],
        *,
        status: str,
    ) -> str:
        plan_id = str(self.cfg.get("pev_plan_id") or "")
        run_id = str(self.cfg.get("pev_run_id") or "")
        target_repo_id = self._immutable_repo_id(target)
        if not plan_id or not run_id:
            raise RuntimeError(
                "Live repository creation requires an approved PEV plan and run"
            )
        if not target_repo_id:
            raise RuntimeError(
                "GitHub did not return an immutable repository id; ownership "
                "cannot be recorded safely"
            )
        self.db.record_repository_ownership(
            source_org=str(
                getattr(self.ado, "org_url", "")
                or self.cfg.get("ado_org_url", "")
            ),
            ado_project=repo.ado_project,
            ado_repo=repo.ado_repo,
            gh_org=repo.gh_org,
            gh_repo=repo.gh_repo,
            plan_id=plan_id,
            run_id=run_id,
            target_repo_id=target_repo_id,
            status=status,
        )
        with self._target_identity_lock:
            key = self._target_lease_key(repo)
            self._target_seen_ids[key] = target_repo_id
            if key not in self._target_use_recorded:
                self.db.record_repository_target_use(
                    repo.gh_org,
                    repo.gh_repo,
                    plan_id=plan_id,
                    run_id=run_id,
                    target_repo_id=target_repo_id,
                    status=status,
                )
                self._target_use_recorded.add(key)
        return target_repo_id

    def _target_owned_by_current_run(
        self, repo: RepoConfig, target: dict[str, Any]
    ) -> bool:
        plan_id = str(self.cfg.get("pev_plan_id") or "")
        run_id = str(self.cfg.get("pev_run_id") or "")
        target_repo_id = self._immutable_repo_id(target)
        return bool(
            plan_id and run_id and target_repo_id
            and self.db.repository_owned_by_run(
                repo.gh_org,
                repo.gh_repo,
                plan_id=plan_id,
                run_id=run_id,
                target_repo_id=target_repo_id,
            )
        )

    def _approved_target_snapshot(self, repo: RepoConfig) -> dict[str, Any]:
        raw = self.target_snapshots.get(
            f"{repo.ado_project}/{repo.ado_repo}"
        )
        if not isinstance(raw, Mapping):
            raise RuntimeError(
                f"Approved target snapshot is missing for "
                f"{repo.ado_project}/{repo.ado_repo}"
            )
        target_exists = raw.get("target_exists")
        target_repo_id = raw.get("target_repo_id")
        target_size = raw.get("target_size")
        target_visibility = raw.get("target_visibility")
        default_branch = raw.get("target_default_branch")
        if (
            not isinstance(target_exists, bool)
            or not isinstance(target_repo_id, str)
            or isinstance(target_size, bool)
            or not isinstance(target_size, int)
            or target_size < 0
            or target_visibility not in {"private", "internal", "public"}
            or not isinstance(default_branch, str)
        ):
            raise RuntimeError("Approved target identity snapshot is invalid")
        branches = self._validated_ref_map(
            raw.get("target_branch_refs"), "heads"
        )
        tags = self._validated_ref_map(raw.get("target_tag_refs"), "tags")
        digest = self._source_ref_digest(branches, tags)
        if not hmac.compare_digest(
            digest, str(raw.get("target_refs_digest", ""))
        ):
            raise RuntimeError("Approved target ref snapshot digest is invalid")
        if branches and (
            not default_branch
            or f"refs/heads/{default_branch}" not in branches
        ):
            raise RuntimeError(
                "Approved target default branch is absent from its ref snapshot"
            )
        if not target_exists and (
            target_repo_id or target_size or target_visibility != "private"
            or default_branch or branches or tags
        ):
            raise RuntimeError("Approved absent-target snapshot is inconsistent")
        if target_exists and not target_repo_id.strip():
            raise RuntimeError("Approved target immutable ID is missing")
        return {
            "target_exists": target_exists,
            "target_repo_id": target_repo_id,
            "target_size": target_size,
            "target_visibility": target_visibility,
            "target_default_branch": default_branch,
            "target_branch_refs": branches,
            "target_tag_refs": tags,
            "target_refs_digest": digest,
        }

    def _observe_github_target_snapshot(
        self, repo: RepoConfig, target: Mapping[str, Any]
    ) -> dict[str, Any]:
        helper = getattr(self.gh, "list_git_refs", None)
        if not callable(helper):
            raise RuntimeError(
                "GitHub client does not support complete paginated ref listing"
            )

        def read(namespace: str) -> dict[str, str]:
            rows = helper(repo.gh_org, repo.gh_repo, namespace)
            if not isinstance(rows, list):
                raise RuntimeError(
                    f"GitHub refs/{namespace} response must be a complete list"
                )
            converted = []
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise RuntimeError(
                        f"GitHub refs/{namespace} item {index} must be an object"
                    )
                obj = row.get("object", {})
                if not isinstance(obj, Mapping):
                    raise RuntimeError(
                        f"GitHub ref {row.get('ref')!r} has no object metadata"
                    )
                converted.append({
                    "name": row.get("ref"),
                    "objectId": obj.get("sha"),
                })
            return self._normalize_ado_ref_rows(converted, namespace)

        raw_size = target.get("size")
        raw_visibility = target.get("visibility")
        raw_default = target.get("default_branch", "")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int) \
                or raw_size < 0 or not isinstance(raw_default, str) \
                or raw_visibility not in {"private", "internal", "public"}:
            raise RuntimeError("GitHub target metadata is malformed")
        branches, tags = read("heads"), read("tags")
        return {
            "target_exists": True,
            "target_repo_id": self._immutable_repo_id(dict(target)),
            "target_size": raw_size,
            "target_visibility": raw_visibility,
            "target_default_branch": raw_default.strip(),
            "target_branch_refs": branches,
            "target_tag_refs": tags,
            "target_refs_digest": self._source_ref_digest(branches, tags),
        }

    def _verify_github_target_snapshot(
        self,
        repo: RepoConfig,
        target: Mapping[str, Any],
        approved: Mapping[str, Any],
    ) -> None:
        observed = self._observe_github_target_snapshot(repo, target)
        mismatches = [
            field for field in (
                "target_repo_id",
                "target_size",
                "target_visibility",
                "target_default_branch",
                "target_branch_refs",
                "target_tag_refs",
                "target_refs_digest",
            )
            if observed[field] != approved[field]
        ]
        if mismatches:
            raise RuntimeError(
                f"GitHub target drift for {repo.gh_org}/{repo.gh_repo}: "
                f"{', '.join(mismatches)} changed after plan approval"
            )

    def _approved_source_ref_snapshot(self, repo: RepoConfig) -> dict[str, Any]:
        snapshots = self.source_ref_snapshots
        if not snapshots:
            raise RuntimeError(
                "Approved PEV source ref snapshots are required for repository migration"
            )
        raw = snapshots.get(f"{repo.ado_project}/{repo.ado_repo}")
        if not isinstance(raw, Mapping):
            raise RuntimeError(
                f"Approved source ref snapshot is missing for "
                f"{repo.ado_project}/{repo.ado_repo}"
            )
        branches = self._validated_ref_map(
            raw.get("source_branch_refs"), "heads"
        )
        tags = self._validated_ref_map(raw.get("source_tag_refs"), "tags")
        digest = self._source_ref_digest(branches, tags)
        if not hmac.compare_digest(
            digest, str(raw.get("source_refs_digest", ""))
        ):
            raise RuntimeError("Approved source ref snapshot digest is invalid")
        repo_id = str(raw.get("source_repo_id", "")).strip()
        if not repo_id:
            raise RuntimeError("Approved source repository ID is missing")
        default_branch = str(raw.get("default_branch", ""))
        expected_head = (
            branches.get(f"refs/heads/{default_branch}", "")
            if default_branch else ""
        )
        if str(raw.get("source_head_sha", "")) != expected_head:
            raise RuntimeError(
                "Approved source HEAD does not match its default-branch ref"
            )
        return {
            "source_repo_id": repo_id,
            "default_branch": default_branch,
            "source_head_sha": expected_head,
            "source_branch_refs": branches,
            "source_tag_refs": tags,
            "source_refs_digest": digest,
        }

    @staticmethod
    def _validated_ref_map(value: Any, namespace: str) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise RuntimeError(f"Approved refs/{namespace} snapshot must be a map")
        prefix = f"refs/{namespace}/"
        result: dict[str, str] = {}
        for raw_name, raw_sha in value.items():
            name, sha = str(raw_name), str(raw_sha).strip().lower()
            if not name.startswith(prefix) or not name[len(prefix):]:
                raise RuntimeError(
                    f"Approved refs/{namespace} snapshot contains invalid ref {name!r}"
                )
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
                raise RuntimeError(f"Approved ref {name!r} has an invalid object ID")
            if name in result:
                raise RuntimeError(f"Approved ref {name!r} is duplicated")
            result[name] = sha
        return dict(sorted(result.items()))

    @staticmethod
    def _source_ref_digest(
        branches: Mapping[str, str], tags: Mapping[str, str]
    ) -> str:
        payload = {
            "branches": [list(item) for item in sorted(branches.items())],
            "tags": [list(item) for item in sorted(tags.items())],
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _observe_ado_source_refs(
        self, repo: RepoConfig, source: Any = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source = source or self.ado.get_repo(repo.ado_project, repo.ado_repo)
        if not isinstance(source, dict):
            raise RuntimeError("ADO repository response must be an object")
        repo_id = str(source.get("id", "")).strip()
        raw_default = source.get("defaultBranch")
        if raw_default in (None, ""):
            default_branch = ""
        elif isinstance(raw_default, str) and raw_default.startswith(
            "refs/heads/"
        ) and raw_default[len("refs/heads/"):]:
            default_branch = raw_default[len("refs/heads/"):]
        else:
            raise RuntimeError("ADO repository returned an invalid defaultBranch")
        helper = getattr(self.ado, "list_refs", None)
        if not repo_id or not callable(helper):
            raise RuntimeError(
                "ADO repository identity and complete ref listing are required"
            )
        branches = self._normalize_ado_ref_rows(
            helper(repo.ado_project, repo_id, "heads/"), "heads"
        )
        tags = self._normalize_ado_ref_rows(
            helper(repo.ado_project, repo_id, "tags/"), "tags"
        )
        snapshot = {
            "source_repo_id": repo_id,
            "default_branch": default_branch,
            "source_head_sha": (
                branches.get(f"refs/heads/{default_branch}", "")
                if default_branch else ""
            ),
            "source_branch_refs": branches,
            "source_tag_refs": tags,
            "source_refs_digest": self._source_ref_digest(branches, tags),
        }
        return source, snapshot

    @staticmethod
    def _normalize_ado_ref_rows(rows: Any, namespace: str) -> dict[str, str]:
        if not isinstance(rows, list):
            raise RuntimeError(f"ADO refs/{namespace} response must be a list")
        prefix = f"refs/{namespace}/"
        normalized: dict[str, str] = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise RuntimeError(f"ADO ref item {index} must be an object")
            name, sha = row.get("name"), row.get("objectId")
            if not isinstance(name, str) or not name.startswith(prefix) \
                    or not name[len(prefix):]:
                raise RuntimeError(
                    f"ADO refs/{namespace} item {index} has an invalid name"
                )
            sha = str(sha or "").strip().lower()
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
                raise RuntimeError(f"ADO ref {name!r} has an invalid object ID")
            if name in normalized:
                raise RuntimeError(f"ADO ref {name!r} is duplicated")
            normalized[name] = sha
        return dict(sorted(normalized.items()))

    def _verify_ado_source_snapshot(
        self,
        repo: RepoConfig,
        approved: Mapping[str, Any],
        source: Any = None,
    ) -> dict[str, Any]:
        source, observed = self._observe_ado_source_refs(repo, source)
        mismatches = [
            field for field in (
                "source_repo_id",
                "default_branch",
                "source_head_sha",
                "source_branch_refs",
                "source_tag_refs",
                "source_refs_digest",
            )
            if observed[field] != approved[field]
        ]
        if mismatches:
            raise RuntimeError(
                f"ADO source drift for {repo.ado_project}/{repo.ado_repo}: "
                f"{', '.join(mismatches)} changed after plan approval"
            )
        return source

    def _migrate_git(self, repo: RepoConfig, **_kw: Any) -> dict:
        """Actually migrate git content via mirror clone + push, or gh gei."""
        log.info("git: %s/%s -> %s/%s [strategy=%s]%s",
                 repo.ado_project, repo.ado_repo,
                 repo.gh_org, repo.gh_repo, self.strategy,
                 " [DRY RUN]" if self.dry_run else "")

        source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
        approved_source = None
        if self.cfg.get("pev_plan_id"):
            approved_source = self._approved_source_ref_snapshot(repo)
            source = self._verify_ado_source_snapshot(
                repo, approved_source, source
            )
        clone_url = source.get("remoteUrl", "")
        default_branch = (
            approved_source["default_branch"]
            if approved_source is not None
            else str(source.get("defaultBranch") or "").removeprefix(
                "refs/heads/"
            )
        )
        repo_stats = self.ado.get_repo_stats(repo.ado_project, source.get("id", ""))

        stats: dict[str, Any] = {
            "strategy": self.strategy,
            "source_url": clone_url,
            "default_branch": default_branch,
            "branches": repo_stats.get("branch_count", 0),
            "size_kb": source.get("size", 0),
        }

        target_exists = self.gh.repo_exists(repo.gh_org, repo.gh_repo)
        stats["target_preexisting"] = target_exists
        approved_target = (
            self._approved_target_snapshot(repo)
            if self.cfg.get("pev_plan_id") else None
        )

        mapping_cfg = self.cfg.get("mapping", {})
        existing_policy = mapping_cfg.get("existing_target_policy", "fail")
        target: dict[str, Any] = {}
        owned_by_current_run = False
        if target_exists:
            target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            owned_by_current_run = self._target_owned_by_current_run(repo, target)
            if approved_target is not None:
                if approved_target["target_exists"]:
                    self._verify_github_target_snapshot(
                        repo, target, approved_target
                    )
                elif not owned_by_current_run:
                    raise RuntimeError(
                        f"Target {repo.gh_org}/{repo.gh_repo} appeared after "
                        "plan approval and is not owned by this exact run"
                    )
            if not owned_by_current_run and existing_policy != "reuse":
                raise RuntimeError(
                    f"Target {repo.gh_org}/{repo.gh_repo} already exists; "
                    "set mapping.existing_target_policy=reuse only after provenance review"
                )
            if (
                int(target.get("size", 0) or 0) > 0
                and not owned_by_current_run
                and not mapping_cfg.get("allow_nonempty_target", False)
            ):
                raise RuntimeError(
                    f"Refusing to overwrite non-empty target {repo.gh_org}/{repo.gh_repo}; "
                    "set mapping.allow_nonempty_target=true only with explicit approval"
                )
        elif approved_target is not None and approved_target["target_exists"]:
            raise RuntimeError(
                f"Approved target {repo.gh_org}/{repo.gh_repo} no longer exists"
            )

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        if not self.cfg.get("pev_plan_id") or not self.cfg.get("pev_run_id"):
            raise RuntimeError(
                "Live repository migration is only available through the "
                "approved Planner-Executor-Validator control plane"
            )

        # GEI owns target creation.  Pre-creating it causes importer conflicts.
        if self.strategy != "gei" and not target_exists:
            def _create_target() -> dict[str, Any]:
                created = self.gh.create_repo(
                    repo.gh_org,
                    repo.gh_repo,
                    private=True,
                    description=(
                        f"Migrated from ADO: "
                        f"{repo.ado_project}/{repo.ado_repo}"
                    ),
                )
                if not isinstance(created, Mapping):
                    raise RuntimeError(
                        "GitHub repository creation returned malformed metadata"
                    )
                created = dict(created)
                if not self._immutable_repo_id(created):
                    observed = self.gh.get_repo(repo.gh_org, repo.gh_repo)
                    if not isinstance(observed, Mapping):
                        raise RuntimeError(
                            "GitHub repository creation could not be read back"
                        )
                    created = dict(observed)
                if created.get("visibility") not in {None, "private"}:
                    raise RuntimeError(
                        "New GitHub repository was not created private"
                    )
                return created

            target = self._dispatch_github_mutation(
                repo,
                "github_create_repository",
                {
                    "gh_org": repo.gh_org,
                    "gh_repo": repo.gh_repo,
                    "visibility": "private",
                },
                _create_target,
                post_success=lambda created: self._record_target_ownership(
                    repo, created, status="created"
                ),
            )
            owned_by_current_run = True

        if self.strategy == "gei":
            self._assert_target_fence(repo)
            stats.update(self._run_gei_migration(repo, source))
            self._assert_target_fence(repo)
            if not target_exists:
                target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
                self._record_target_ownership(repo, target, status="created")
                owned_by_current_run = True
        else:
            stats.update(self._run_mirror_migration(repo, clone_url))

        # Apply team mappings
        team_failures = 0
        for ado_team, target_access in repo.team_mapping.items():
            if not isinstance(target_access, Mapping):
                raise RuntimeError(
                    f"Team mapping {ado_team!r} is not canonical plan data"
                )
            gh_team = str(target_access.get("github_team") or "").strip()
            permission = str(target_access.get("permission") or "").strip()
            if not gh_team or permission not in {
                "pull", "triage", "push", "maintain", "admin",
            }:
                raise RuntimeError(
                    f"Team mapping {ado_team!r} has invalid target access"
                )
            try:
                self._apply_github_team_access(
                    repo, gh_team, permission
                )
            except Exception as exc:
                log.warning("team mapping %s -> %s failed: %s", ado_team, gh_team, exc)
                team_failures += 1
        if team_failures:
            stats["failed"] = team_failures
            stats["team_mapping_failures"] = team_failures
        if not repo.access_policy_approved:
            stats["requires_review"] = True
            stats["access_control_review_required"] = True
            stats["review_reason"] = (
                "ADO repository ACLs cannot be inferred safely from team names. "
                "Approve the target organization base permission and every "
                "explicit principal/role mapping in the immutable plan before "
                "this repository may pass cutover validation."
            )

        # Verify: check that the default branch exists on target
        gh_branches = self.gh.list_branches(repo.gh_org, repo.gh_repo)
        if not isinstance(gh_branches, list):
            raise RuntimeError("GitHub branch verification returned malformed data")
        gh_branch_names = [b.get("name", "") for b in gh_branches]
        stats["gh_branches"] = len(gh_branch_names)
        source_empty = int(repo_stats.get("branch_count", 0) or 0) == 0
        stats["default_branch_present"] = (
            default_branch in gh_branch_names or (source_empty and not gh_branch_names)
        )
        stats["default_branch_not_applicable"] = source_empty and not gh_branch_names
        if not stats["default_branch_present"]:
            raise RuntimeError(
                f"Target default branch {default_branch!r} is absent after migration"
            )

        if owned_by_current_run:
            if not target:
                target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            self._record_target_ownership(repo, target, status="migrated")

        return stats

    def _run_mirror_migration(self, repo: RepoConfig, clone_url: str) -> dict:
        """Execute git clone --mirror && git push --mirror."""
        clone_url = self._validated_ado_clone_url(repo, clone_url)

        gh_web_url = str(self.cfg.get("gh_web_url", "")).rstrip("/")
        if not gh_web_url:
            api_base = str(getattr(self.gh, "BASE", "https://api.github.com"))
            if api_base.rstrip("/") == "https://api.github.com":
                gh_web_url = "https://github.com"
            elif api_base.endswith("/api/v3"):
                gh_web_url = api_base[:-7]
            else:
                raise RuntimeError(
                    "global.gh_web_url is required for a non-github.com target"
                )
        web = urlsplit(gh_web_url)
        api = urlsplit(str(getattr(self.gh, "BASE", "https://api.github.com")))
        expected_web_host = (
            "github.com" if (api.hostname or "").casefold() == "api.github.com"
            else (api.hostname or "")
        )
        if (
            web.scheme.lower() != "https"
            or not web.hostname
            or web.username
            or web.password
            or web.query
            or web.fragment
            or web.hostname.casefold() != expected_web_host.casefold()
            or (web.port or 443) != (api.port or 443)
        ):
            raise RuntimeError(
                "GitHub web clone authority does not match the authenticated "
                "GitHub API authority"
            )
        target_url = self._canonical_git_https_url(
            f"{gh_web_url}/{repo.gh_org}/{repo.gh_repo}.git",
            "GitHub target clone URL",
        )
        source_lfs_url = self._lfs_endpoint_for_remote(
            clone_url, "ADO source clone URL"
        )
        target_lfs_url = self._lfs_endpoint_for_remote(
            target_url, "GitHub target clone URL"
        )

        tmpdir = tempfile.mkdtemp(prefix="ado2gh_mirror_")
        mirror_path = os.path.join(tmpdir, f"{repo.ado_repo}.git")

        try:
            source_env = self._git_auth_env(
                tmpdir,
                "source",
                "ado2gh",
                self.ado.pat,
                clone_url,
                source_lfs_url,
            )
            source_audit_env = self._credential_free_git_env(source_env)
            # Clone mirror from ADO
            result = subprocess.run(
                ["git", "clone", "--mirror", clone_url, mirror_path],
                capture_output=True, text=True, timeout=1800,
                env=source_env,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone --mirror failed: {result.stderr[:500]}")

            self._verify_local_remote_url(
                mirror_path, "origin", clone_url, source_audit_env
            )

            approved_source = self._approved_source_ref_snapshot(repo)
            self._verify_local_source_snapshot(
                mirror_path, repo, approved_source, source_audit_env
            )
            self._validate_repository_lfs_configs(
                mirror_path,
                approved_source,
                source_lfs_url,
                source_audit_env,
            )

            # Mirror clones may contain advertised PR/hidden refs that are not
            # authorized by the plan.  Build the inventory from the exact
            # approved heads/tags and every commit reachable from them.  This
            # covers historical LFS versions.  Git LFS's migration-scoped
            # ``--all`` mode is then invoked only with these non-empty exact
            # roots; it is never allowed to fall back to every local ref (which
            # would include refs/pull and other hidden mirror namespaces).
            approved_lfs_ref_map = {
                **approved_source["source_branch_refs"],
                **approved_source["source_tag_refs"],
            }
            approved_lfs_revisions = self._approved_lfs_revisions(
                mirror_path, approved_lfs_ref_map, source_audit_env
            )
            approved_lfs_oids = self._inventory_lfs_oids(
                mirror_path, approved_lfs_revisions, source_audit_env
            )
            if approved_lfs_oids:
                self._fetch_all_lfs_for_approved_refs(
                    mirror_path,
                    "origin",
                    sorted(approved_lfs_ref_map),
                    source_env,
                    purpose="approved ADO history",
                )
                self._verify_local_lfs_objects(
                    mirror_path,
                    approved_lfs_oids,
                    "source LFS fetch",
                )

            # Push mirror to GitHub
            result = subprocess.run(
                ["git", "remote", "set-url", "origin", target_url],
                capture_output=True, text=True, cwd=mirror_path, timeout=30,
                env=source_audit_env,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git remote set-url failed: {result.stderr[:500]}")

            target_env = self._git_auth_env(
                tmpdir,
                "target",
                "x-access-token",
                self.gh.token_manager.get_token(),
                target_url,
                target_lfs_url,
            )
            target_audit_env = self._credential_free_git_env(target_env)
            self._verify_local_remote_url(
                mirror_path, "origin", target_url, target_audit_env
            )

            # Push branches + tags only — explicitly NOT a full mirror.
            # `git push --mirror` does `--prune` on every ref namespace and
            # tries to delete refs that don't exist in source, which:
            #  - wipes the ado2gh/migrated-workflows branch we created on
            #    the destination during a previous `push-workflows` run, and
            #  - fails on GitHub's read-only refs/pull/*/merge refs (e.g.
            #    "deny updating a hidden ref").
            # Pushing only refs/heads/* and refs/tags/* is non-destructive
            # for our own branches and skips the hidden GitHub PR refs.
            # `git clone --mirror` sets remote.origin.mirror=true in the
            # local config, which makes `git push` always do a mirror push
            # regardless of refspecs. Unset that first.
            subprocess.run(
                ["git", "config", "--unset", "remote.origin.mirror"],
                capture_output=True, text=True, cwd=mirror_path, timeout=10,
                env=target_audit_env,
            )
            approved_target = self._effective_target_ref_baseline(
                repo, approved_source
            )
            source_refs = {
                **approved_source["source_branch_refs"],
                **approved_source["source_tag_refs"],
            }
            target_refs = {
                **approved_target["target_branch_refs"],
                **approved_target["target_tag_refs"],
            }
            updates = [
                (ref_name, target_refs.get(ref_name, ""), f"{ref_name}:{ref_name}")
                for ref_name in sorted(source_refs)
            ]
            self._push_ref_updates(
                repo,
                mirror_path,
                target_env,
                updates,
                operation_kind="git_push_source_refs",
            )

            # Sync the destination's default branch with the source. If the
            # destination repo was created and a non-main branch (e.g. our
            # ado2gh/migrated-workflows from a prior partial run) was the
            # first ref pushed, GitHub set that as the default — and pushing
            # main later doesn't auto-correct it. Force the alignment.
            source_default = self._source_default_branch(
                mirror_path, target_audit_env
            )
            if source_default:
                current = self.gh.get_default_branch(repo.gh_org, repo.gh_repo)
                if current != source_default:
                    expected_current = approved_target.get(
                        "target_default_branch", ""
                    )
                    if approved_target.get("target_exists") \
                            and current != expected_current:
                        raise RuntimeError(
                            f"GitHub target default branch changed concurrently: "
                            f"expected {expected_current!r}, observed {current!r}"
                        )
                    def _set_and_verify_default_branch() -> dict[str, Any]:
                        response = self.gh.set_default_branch(
                            repo.gh_org, repo.gh_repo, source_default,
                        )
                        observed_default = self.gh.get_default_branch(
                            repo.gh_org, repo.gh_repo
                        )
                        if observed_default != source_default:
                            raise RuntimeError(
                                "GitHub default-branch update did not reach its "
                                "requested terminal state"
                            )
                        return (
                            dict(response)
                            if isinstance(response, Mapping) else {}
                        )

                    self._dispatch_github_mutation(
                        repo,
                        "set_default_branch",
                        {"before": current, "after": source_default},
                        _set_and_verify_default_branch,
                    )
                    log.info(
                        "default_branch %s/%s: %s -> %s",
                        repo.gh_org, repo.gh_repo, current, source_default,
                    )

            # Remove only refs which were explicitly approved in the target
            # baseline and are absent from the source. Per-ref leases ensure a
            # collaborator's newer ref is never deleted.
            deletions = [
                (ref_name, target_refs[ref_name], f":{ref_name}")
                for ref_name in sorted(set(target_refs) - set(source_refs))
            ]
            self._push_ref_updates(
                repo,
                mirror_path,
                target_env,
                deletions,
                operation_kind="git_delete_approved_extra_refs",
            )

            # Always inspect every reachable ref. ``skip_lfs`` is accepted
            # only when that complete scan proves there are no LFS pointers.
            lfs_stats = self._push_lfs_objects(
                mirror_path,
                target_url,
                target_env,
                repo=repo,
                skip_push=repo.skip_lfs,
                lfs_url=target_lfs_url,
                approved_refs=source_refs,
                approved_revisions=approved_lfs_revisions,
                approved_lfs_oids=approved_lfs_oids,
            )

            return {"mirror": "success", **lfs_stats}

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _effective_target_ref_baseline(
        self,
        repo: RepoConfig,
        approved_source: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return the only target state a force-with-lease push may replace.

        Existing repositories use the immutable plan snapshot. A repository
        created by this same run may contain a safely resumable subset of the
        exact source refs after a crash; any other ref or SHA is treated as an
        external write and blocks resume.
        """
        approved = self._approved_target_snapshot(repo)
        if approved["target_exists"]:
            return approved
        target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
        if not isinstance(target, Mapping) or not self._target_owned_by_current_run(
            repo, dict(target)
        ):
            raise RuntimeError(
                "Absent-plan target is not owned by the current PEV run"
            )
        observed = self._observe_github_target_snapshot(repo, target)
        source_refs = {
            **approved_source["source_branch_refs"],
            **approved_source["source_tag_refs"],
        }
        observed_refs = {
            **observed["target_branch_refs"],
            **observed["target_tag_refs"],
        }
        unexpected = sorted(
            name for name, sha in observed_refs.items()
            if source_refs.get(name) != sha
        )
        if unexpected:
            raise RuntimeError(
                "Run-owned target contains refs not proven to be a prior exact "
                "source push: " + ", ".join(unexpected[:20])
            )
        return observed

    @staticmethod
    def _chunk_ref_updates(
        updates: list[tuple[str, str, str]],
        max_command_chars: int = 20_000,
    ) -> list[list[tuple[str, str, str]]]:
        batches: list[list[tuple[str, str, str]]] = []
        current: list[tuple[str, str, str]] = []
        current_size = 64
        for item in updates:
            ref_name, expected_sha, refspec = item
            item_size = len(ref_name) + len(expected_sha) + len(refspec) + 32
            if current and current_size + item_size > max_command_chars:
                batches.append(current)
                current = []
                current_size = 64
            current.append(item)
            current_size += item_size
        if current:
            batches.append(current)
        return batches

    def _push_ref_updates(
        self,
        repo: RepoConfig,
        mirror_path: str,
        target_env: dict[str, str],
        updates: list[tuple[str, str, str]],
        *,
        operation_kind: str,
    ) -> None:
        """Push/delete approved refs with exact per-ref compare-and-swap."""
        for batch_index, batch in enumerate(self._chunk_ref_updates(updates), 1):
            self._assert_target_write_boundary(repo)
            self._extend_target_fence(repo, 3_660)
            digest_payload = [list(item) for item in batch]
            operation = self._begin_remote_operation(
                repo,
                operation_kind,
                {
                    "batch": batch_index,
                    "updates_digest": hashlib.sha256(
                        json.dumps(
                            digest_payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    "update_count": len(batch),
                },
            )
            lease_args = [
                f"--force-with-lease={ref_name}:{expected_sha}"
                for ref_name, expected_sha, _ in batch
            ]
            refspecs = [refspec for _, _, refspec in batch]
            try:
                result = subprocess.run(
                    ["git", "push", "--atomic", *lease_args, "origin", *refspecs],
                    capture_output=True,
                    text=True,
                    cwd=mirror_path,
                    timeout=3600,
                    env=target_env,
                )
            except subprocess.TimeoutExpired as exc:
                self._quarantine_target_fence(repo, 0)
                raise RuntimeError(
                    "git push timed out; target quarantined pending reconciliation"
                ) from exc
            if result.returncode != 0:
                self._quarantine_target_fence(repo, 0)
                raise RuntimeError(
                    f"git push compare-and-swap failed: {result.stderr[:500]}"
                )
            self._finish_remote_operation(
                repo,
                operation,
                {"returncode": 0, "update_count": len(batch)},
            )
            self._assert_target_fence(repo)

    def _validated_ado_clone_url(
        self, repo: RepoConfig, clone_url: str
    ) -> str:
        """Bind the PAT-bearing clone endpoint to the approved ADO authority."""
        approved_url = str(
            getattr(self.ado, "org_url", "")
            or self.cfg.get("ado_org_url", "")
        ).rstrip("/")
        approved = urlsplit(approved_url)
        raw_candidate = str(clone_url or "")
        candidate = urlsplit(raw_candidate)
        if (
            approved.scheme.lower() != "https"
            or not approved.hostname
            or candidate.scheme.lower() != "https"
            or not candidate.hostname
            or candidate.password is not None
            or candidate.query
            or candidate.fragment
            or any(
                ord(char) < 32 or ord(char) == 127
                for char in raw_candidate + unquote(candidate.path)
            )
        ):
            raise RuntimeError(
                "Mirror clone URL must be credential-free HTTPS on the approved "
                "ADO organization authority"
            )
        approved_port = approved.port or 443
        candidate_port = candidate.port or 443
        if (
            candidate.hostname.casefold() != approved.hostname.casefold()
            or candidate_port != approved_port
        ):
            raise RuntimeError(
                "ADO clone URL authority does not match the approved organization; "
                "source credential dispatch blocked"
            )
        approved_parts = [
            unquote(part).casefold()
            for part in approved.path.split("/") if part
        ]
        clone_parts = [unquote(part) for part in candidate.path.split("/") if part]
        folded_parts = [part.casefold() for part in clone_parts]
        if approved_parts and folded_parts[:len(approved_parts)] != approved_parts:
            raise RuntimeError(
                "ADO clone URL path is outside the approved organization"
            )
        if (
            len(clone_parts) < 3
            or folded_parts[-2] != "_git"
            or clone_parts[-3].casefold() != repo.ado_project.casefold()
            or clone_parts[-1].removesuffix(".git").casefold()
            != repo.ado_repo.casefold()
        ):
            raise RuntimeError(
                "ADO clone URL does not match the approved project/repository mapping"
            )
        host = candidate.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if candidate_port == 443 else f"{host}:{candidate_port}"
        # ADO sometimes emits an organization hint as URL username. It is not
        # authentication and is stripped before the askpass credential is used.
        return self._canonical_git_https_url(
            urlunsplit(("https", netloc, candidate.path, "", "")),
            "ADO clone URL",
        )

    @staticmethod
    def _sanitized_child_env() -> dict[str, str]:
        """Inherit process settings without unrelated credential variables."""
        sensitive_env_name = re.compile(
            r"(?i)(?:^|_)(?:pat|token|secret|password|passwd|api_key|"
            r"private_key|access_key|credentials)(?:$|_)"
        )
        return {
            key: value for key, value in os.environ.items()
            if not sensitive_env_name.search(key)
        }

    @staticmethod
    def _canonical_git_https_url(raw_url: str, label: str) -> str:
        """Return a credential-free HTTPS URL with canonical authority."""
        raw_url = str(raw_url or "")
        parsed = urlsplit(raw_url)
        raw_path = parsed.path
        if re.search(r"%(?![0-9A-Fa-f]{2})", raw_path):
            raise RuntimeError(f"{label} contains an invalid percent escape")
        decoded_path = raw_path
        for _ in range(4):
            next_path = unquote(decoded_path)
            if next_path == decoded_path:
                break
            # Encoded path separators can be interpreted at a different layer
            # than Git's credential matching and turn an apparently approved
            # repository path into another endpoint.
            if next_path.count("/") != decoded_path.count("/"):
                raise RuntimeError(f"{label} contains an encoded path separator")
            decoded_path = next_path
        else:
            raise RuntimeError(f"{label} contains excessive URL encoding")
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(char) < 32 or ord(char) == 127 for char in raw_url)
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded_path)
        ):
            raise RuntimeError(
                f"{label} must be credential-free HTTPS without control "
                "characters, query, or fragment"
            )
        if (
            "\\" in decoded_path
            or "//" in decoded_path
            or any(part in {".", ".."} for part in decoded_path.split("/"))
        ):
            raise RuntimeError(f"{label} contains an ambiguous repository path")
        try:
            port = parsed.port or 443
        except ValueError as exc:
            raise RuntimeError(f"{label} contains an invalid port") from exc
        host = parsed.hostname.casefold()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if port == 443 else f"{host}:{port}"
        # Produce one stable, shell-safe spelling of the path. This also keeps
        # attacker-controlled repository names out of the Windows askpass
        # launcher's command syntax.
        path = quote(decoded_path, safe="/-._~").rstrip("/")
        if not path or path == "/":
            raise RuntimeError(f"{label} must include an exact repository path")
        return urlunsplit(("https", netloc, path, "", ""))

    @classmethod
    def _lfs_endpoint_for_remote(cls, remote_url: str, label: str) -> str:
        remote = cls._canonical_git_https_url(remote_url, label)
        return remote.rstrip("/") + "/info/lfs"

    @staticmethod
    def _git_config_env(
        base: dict[str, str], pairs: list[tuple[str, str]]
    ) -> dict[str, str]:
        env = {
            key: value for key, value in base.items()
            if not key.startswith("GIT_CONFIG_KEY_")
            and not key.startswith("GIT_CONFIG_VALUE_")
            and key != "GIT_CONFIG_COUNT"
        }
        env["GIT_CONFIG_COUNT"] = str(len(pairs))
        for index, (key, value) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env

    @staticmethod
    def _isolated_git_env(
        tmpdir: str,
        label: str,
        *,
        lfs_url: str = "",
    ) -> dict[str, str]:
        """Build a config-isolated, credential-free Git child environment."""
        root = Path(tmpdir)
        home = root / f"git_home_{label}"
        xdg = home / "xdg"
        hooks = home / "hooks"
        home.mkdir(parents=True, exist_ok=True)
        xdg.mkdir(parents=True, exist_ok=True)
        hooks.mkdir(parents=True, exist_ok=True)
        global_config = home / "global.gitconfig"
        global_config.touch(exist_ok=True)
        inherited = {
            key: value
            for key, value in MigrationEngine._sanitized_child_env().items()
            if not key.startswith("GIT_")
            and not key.startswith("GCM_")
            and key not in {
                "HOME", "USERPROFILE", "XDG_CONFIG_HOME", "SSH_ASKPASS",
                "NETRC", "CURL_HOME",
            }
        }
        base = {
            **inherited,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LC_ALL": "C",
            "LANG": "C",
        }
        pairs = [
            ("credential.helper", ""),
            ("credential.useHttpPath", "true"),
            ("credential.protectProtocol", "true"),
            ("credential.interactive", "never"),
            ("http.followRedirects", "false"),
            ("http.sslVerify", "true"),
            ("protocol.allow", "never"),
            ("protocol.https.allow", "always"),
            ("core.hooksPath", str(hooks)),
            ("lfs.basictransfersonly", "true"),
            ("lfs.transfer.enablehrefrewrite", "false"),
            ("lfs.remote.autodetect", "false"),
            ("lfs.remote.searchall", "false"),
            ("lfs.cachecredentials", "false"),
            ("lfs.skipdownloaderrors", "false"),
            ("lfs.allowincompletepush", "false"),
            ("lfs.fetchinclude", ""),
            ("lfs.fetchexclude", ""),
            ("lfs.fetchrecentalways", "false"),
            ("lfs.fetchrecentremoterefs", "false"),
            ("lfs.fetchrecentcommitsdays", "0"),
            ("lfs.fetchrecentrefsdays", "0"),
            ("lfs.sshtransfer", "never"),
            ("remote.lfsdefault", "origin"),
            ("remote.lfspushdefault", "origin"),
        ]
        if lfs_url:
            pairs.extend((("lfs.url", lfs_url), ("lfs.pushurl", lfs_url)))
        return MigrationEngine._git_config_env(base, pairs)

    @staticmethod
    def _git_auth_env(
        tmpdir: str,
        label: str,
        username: str,
        password: str,
        approved_url: str,
        lfs_url: str,
    ) -> dict[str, str]:
        """Build an isolated, authority-bound askpass environment.

        Secrets remain in the child environment (as required by Git) but never
        appear in argv, remote configuration, exception messages, or the helper
        file. The helper refuses prompts outside the approved repository
        authority/path so redirects cannot reuse the credential.
        """
        approved_url = MigrationEngine._canonical_git_https_url(
            approved_url, f"{label} Git credential authority"
        )
        lfs_url = MigrationEngine._canonical_git_https_url(
            lfs_url, f"{label} Git LFS endpoint"
        )
        root = Path(tmpdir)
        helper_py = root / f"askpass_{label}.py"
        helper_py.write_text(
            "import os, re, sys\n"
            "from urllib.parse import unquote, urlsplit\n"
            "prompt_raw = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "prompt = prompt_raw.lower()\n"
            "match = re.search(r\"'([^']+)'\", prompt_raw)\n"
            "if not match:\n"
            "    raise SystemExit(1)\n"
            "raw = match.group(1)\n"
            "observed = urlsplit(raw)\n"
            "approved = urlsplit(os.environ.get('ADO2GH_GIT_ALLOWED_URL', ''))\n"
            "def authority(value):\n"
            "    return (value.scheme.lower(), (value.hostname or '').lower(), "
            "value.port or 443)\n"
            "def safe_path(value):\n"
            "    current = value.path\n"
            "    if re.search(r'%(?![0-9A-Fa-f]{2})', current):\n"
            "        raise ValueError('invalid escape')\n"
            "    for _ in range(4):\n"
            "        decoded = unquote(current)\n"
            "        if decoded == current:\n"
            "            break\n"
            "        if decoded.count('/') != current.count('/'):\n"
            "            raise ValueError('encoded separator')\n"
            "        current = decoded\n"
            "    else:\n"
            "        raise ValueError('excessive encoding')\n"
            "    if ('\\\\' in current or '//' in current or any(part in "
            "{'.', '..'} for part in current.split('/')) or any(ord(c) < 32 "
            "or ord(c) == 127 for c in current)):\n"
            "        raise ValueError('ambiguous path')\n"
            "    return current.rstrip('/')\n"
            "try:\n"
            "    allowed_path = safe_path(approved)\n"
            "    observed_path = safe_path(observed)\n"
            "    observed_user = unquote(observed.username or '')\n"
            "    allowed_user = os.environ.get('ADO2GH_GIT_USERNAME', '')\n"
            "    valid = (authority(observed) == authority(approved) and "
            "observed.scheme.lower() == 'https' and observed.password is None "
            "and observed_user in {'', allowed_user} and not observed.query and "
            "not observed.fragment and not any(ord(c) < 32 or ord(c) == 127 "
            "for c in raw))\n"
            "except (TypeError, ValueError):\n"
            "    valid = False\n"
            "if not valid:\n"
            "    raise SystemExit(1)\n"
            "if observed_path and allowed_path and not (observed_path == "
            "allowed_path or observed_path.startswith(allowed_path + '/')):\n"
            "    raise SystemExit(1)\n"
            "if 'username' in prompt:\n"
            "    key = 'ADO2GH_GIT_USERNAME'\n"
            "elif 'password' in prompt:\n"
            "    key = 'ADO2GH_GIT_PASSWORD'\n"
            "else:\n"
            "    raise SystemExit(1)\n"
            "sys.stdout.write(os.environ.get(key, '') + '\\n')\n",
            encoding="utf-8",
        )

        if os.name == "nt":
            launcher = root / f"askpass_{label}.cmd"
            launcher.write_text(
                f'@"{sys.executable}" "{helper_py}" "%~1"\r\n',
                encoding="utf-8",
            )
        else:
            launcher = root / f"askpass_{label}.sh"
            launcher.write_text(
                "#!/bin/sh\n"
                f'exec "{sys.executable}" "{helper_py}" "$1"\n',
                encoding="utf-8",
            )
            launcher.chmod(0o700)

        return {
            **MigrationEngine._isolated_git_env(
                tmpdir, label, lfs_url=lfs_url
            ),
            "GIT_ASKPASS": str(launcher),
            "GIT_ASKPASS_REQUIRE": "force",
            "ADO2GH_GIT_ALLOWED_URL": approved_url,
            "ADO2GH_GIT_USERNAME": username,
            "ADO2GH_GIT_PASSWORD": password,
        }

    @staticmethod
    def _credential_free_git_env(
        auth_env: Mapping[str, str]
    ) -> dict[str, str]:
        return {
            key: value for key, value in auth_env.items()
            if key not in {
                "ADO2GH_GIT_ALLOWED_URL", "ADO2GH_GIT_USERNAME",
                "ADO2GH_GIT_PASSWORD", "GIT_ASKPASS", "GIT_ASKPASS_REQUIRE",
            }
        }

    @classmethod
    def _validate_lfs_config_content(
        cls,
        content: str,
        approved_lfs_url: str,
        source_ref: str,
        env: Mapping[str, str],
    ) -> None:
        """Reject repository-controlled LFS network/transfer overrides."""
        if len(content.encode("utf-8")) > 64 * 1024:
            raise RuntimeError(
                f".lfsconfig on {source_ref} exceeds the security review limit"
            )
        parsed = subprocess.run(
            ["git", "config", "--file", "-", "--null", "--list"],
            input=content,
            capture_output=True,
            text=True,
            timeout=10,
            env=dict(env),
        )
        if parsed.returncode != 0:
            raise RuntimeError(f".lfsconfig on {source_ref} is malformed")
        approved = cls._canonical_git_https_url(
            approved_lfs_url, "approved source Git LFS endpoint"
        )
        safe_non_network = {
            "lfs.fetchinclude", "lfs.fetchexclude", "lfs.locksverify",
        }
        for row in parsed.stdout.split("\0"):
            if not row:
                continue
            if "\n" not in row:
                raise RuntimeError(
                    f".lfsconfig on {source_ref} contains a malformed entry"
                )
            key, value = row.split("\n", 1)
            key = key.casefold()
            is_url = key in {"lfs.url", "lfs.pushurl"} or bool(
                re.fullmatch(r"remote\.[^.]+\.lfs(?:push)?url", key)
            )
            if is_url:
                candidate = cls._canonical_git_https_url(
                    value, f".lfsconfig {key} on {source_ref}"
                )
                if not hmac.compare_digest(candidate, approved):
                    raise RuntimeError(
                        f"Unsafe .lfsconfig {key} override on {source_ref}; "
                        "endpoint is outside the approved source repository"
                    )
            elif key not in safe_non_network:
                raise RuntimeError(
                    f"Unsafe .lfsconfig setting {key} on {source_ref}; "
                    "network, transfer-agent, and failure-masking settings "
                    "are not permitted during migration"
                )

    def _validate_repository_lfs_configs(
        self,
        mirror_path: str,
        approved_source: Mapping[str, Any],
        approved_lfs_url: str,
        env: Mapping[str, str],
    ) -> None:
        refs = sorted({
            *approved_source["source_branch_refs"],
            *approved_source["source_tag_refs"],
        })
        checked_blobs: set[str] = set()
        for source_ref in refs:
            tree = subprocess.run(
                [
                    "git", "ls-tree", "-z", "--full-tree", source_ref,
                    "--", ".lfsconfig",
                ],
                capture_output=True,
                text=True,
                cwd=mirror_path,
                timeout=30,
                env=dict(env),
            )
            if tree.returncode != 0:
                raise RuntimeError(
                    f"Cannot inspect .lfsconfig on approved ref {source_ref}"
                )
            if not tree.stdout:
                continue
            entries = [item for item in tree.stdout.split("\0") if item]
            if len(entries) != 1 or "\t" not in entries[0]:
                raise RuntimeError(
                    f"Ambiguous .lfsconfig tree entry on {source_ref}"
                )
            metadata, path = entries[0].split("\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[1] != "blob" or path != ".lfsconfig":
                raise RuntimeError(
                    f".lfsconfig on {source_ref} is not a regular blob"
                )
            blob_id = fields[2]
            if blob_id in checked_blobs:
                continue
            checked_blobs.add(blob_id)
            blob = subprocess.run(
                ["git", "cat-file", "blob", blob_id],
                capture_output=True,
                text=True,
                cwd=mirror_path,
                timeout=30,
                env=dict(env),
            )
            if blob.returncode != 0:
                raise RuntimeError(
                    f"Cannot read .lfsconfig on approved ref {source_ref}"
                )
            self._validate_lfs_config_content(
                blob.stdout, approved_lfs_url, source_ref, env
            )

    @classmethod
    def _verify_local_remote_url(
        cls,
        mirror_path: str,
        remote: str,
        expected_url: str,
        env: Mapping[str, str],
    ) -> None:
        observed = subprocess.run(
            ["git", "remote", "get-url", "--all", remote],
            capture_output=True,
            text=True,
            cwd=mirror_path,
            timeout=30,
            env=dict(env),
        )
        if observed.returncode != 0:
            raise RuntimeError(f"Cannot verify Git remote {remote!r}")
        urls = [line.strip() for line in observed.stdout.splitlines() if line.strip()]
        expected = cls._canonical_git_https_url(
            expected_url, f"expected Git remote {remote}"
        )
        if len(urls) != 1 or not hmac.compare_digest(
            cls._canonical_git_https_url(
                urls[0], f"observed Git remote {remote}"
            ),
            expected,
        ):
            raise RuntimeError(
                f"Git remote {remote!r} is not the exact approved endpoint"
            )

    @staticmethod
    def _source_default_branch(
        mirror_path: str, env: Optional[Mapping[str, str]] = None
    ) -> str:
        """Read HEAD's symbolic-ref from a bare/mirror clone -> branch name."""
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, cwd=mirror_path, timeout=10,
            env=dict(env) if env is not None else None,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _verify_local_source_snapshot(
        self,
        mirror_path: str,
        repo: RepoConfig,
        approved: Mapping[str, Any],
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        result = subprocess.run(
            [
                "git", "for-each-ref",
                "--format=%(refname)%09%(objectname)",
                "refs/heads/", "refs/tags/",
            ],
            capture_output=True,
            text=True,
            cwd=mirror_path,
            timeout=60,
            env=dict(env) if env is not None else None,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Cannot inspect cloned source refs: {result.stderr[:500]}"
            )
        branches: dict[str, str] = {}
        tags: dict[str, str] = {}
        for line in result.stdout.splitlines():
            try:
                name, sha = line.split("\t", 1)
            except ValueError as exc:
                raise RuntimeError("git returned a malformed local ref row") from exc
            target = branches if name.startswith("refs/heads/") else (
                tags if name.startswith("refs/tags/") else None
            )
            if target is None or name in target:
                raise RuntimeError(f"git returned an invalid local ref {name!r}")
            sha = sha.strip().lower()
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
                raise RuntimeError(f"git returned an invalid SHA for {name!r}")
            target[name] = sha
        branches, tags = dict(sorted(branches.items())), dict(sorted(tags.items()))
        local_default = self._source_default_branch(mirror_path, env)
        mismatches = []
        if branches != approved["source_branch_refs"]:
            mismatches.append("branches")
        if tags != approved["source_tag_refs"]:
            mismatches.append("tags")
        if approved["default_branch"] and local_default != approved["default_branch"]:
            mismatches.append("default_branch")
        if mismatches:
            raise RuntimeError(
                f"Cloned source drift for {repo.ado_project}/{repo.ado_repo}: "
                f"{', '.join(mismatches)} differ from the approved plan; push blocked"
            )

    @staticmethod
    def _validated_lfs_ref_inventory(
        approved_refs: Optional[Mapping[str, str]],
    ) -> dict[str, str]:
        if not isinstance(approved_refs, Mapping):
            raise RuntimeError(
                "Plan-scoped LFS migration requires an explicit approved "
                "head/tag ref inventory"
            )
        refs: dict[str, str] = {}
        for raw_name, raw_object_id in approved_refs.items():
            ref_name = str(raw_name)
            object_id = str(raw_object_id).strip().lower()
            if (
                not ref_name.startswith(("refs/heads/", "refs/tags/"))
                or not ref_name.split("/", 2)[-1]
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id)
            ):
                raise RuntimeError("Approved LFS ref inventory is malformed")
            refs[ref_name] = object_id
        return dict(sorted(refs.items()))

    @classmethod
    def _approved_lfs_revisions(
        cls,
        mirror_path: str,
        approved_refs: Optional[Mapping[str, str]],
        audit_env: Mapping[str, str],
    ) -> list[str]:
        """Return only approved refs and commits reachable from those refs."""
        refs = cls._validated_lfs_ref_inventory(approved_refs)
        if not refs:
            # Never invoke rev-list without roots: Git would otherwise be free
            # to select HEAD, which is not plan evidence for an empty repo.
            return []
        roots = list(refs)
        try:
            result = subprocess.run(
                ["git", "rev-list", "--topo-order", "--reverse", "--stdin"],
                input="".join(f"{ref_name}\n" for ref_name in roots),
                capture_output=True,
                text=True,
                cwd=mirror_path,
                timeout=600,
                env=dict(audit_env),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "Cannot enumerate commits reachable from approved LFS refs"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                "Cannot enumerate commits reachable from approved LFS refs: "
                f"{result.stderr[:300]}"
            )
        commits: list[str] = []
        seen: set[str] = set()
        for line in result.stdout.splitlines():
            object_id = line.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
                raise RuntimeError(
                    "git rev-list returned a malformed reachable commit ID"
                )
            if object_id not in seen:
                seen.add(object_id)
                commits.append(object_id)
        # Keep the named refs as roots as well.  This covers an approved tag
        # that peels to a tree even when it has no reachable commit.
        return [*roots, *commits]

    @staticmethod
    def _chunk_lfs_revisions(
        revisions: list[str], max_command_chars: int = 20_000
    ) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        current_size = 128
        for revision in revisions:
            item_size = len(revision) + 1
            if item_size + 128 > max_command_chars:
                raise RuntimeError("Approved LFS revision exceeds command limit")
            if current and current_size + item_size > max_command_chars:
                batches.append(current)
                current = []
                current_size = 128
            current.append(revision)
            current_size += item_size
        if current:
            batches.append(current)
        return batches

    @classmethod
    def _fetch_all_lfs_for_approved_refs(
        cls,
        mirror_path: str,
        remote: str,
        approved_ref_names: list[str],
        auth_env: Mapping[str, str],
        *,
        purpose: str,
    ) -> None:
        if remote != "origin":
            raise RuntimeError("LFS migration may only use the fenced origin remote")
        for ref_name in approved_ref_names:
            if not ref_name.startswith(("refs/heads/", "refs/tags/")):
                raise RuntimeError("Approved LFS ref inventory is malformed")
        for batch in cls._chunk_lfs_revisions(approved_ref_names):
            try:
                result = subprocess.run(
                    ["git", "lfs", "fetch", "--all", remote, *batch],
                    capture_output=True,
                    text=True,
                    cwd=mirror_path,
                    timeout=3600,
                    env=dict(auth_env),
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(
                    f"Cannot fetch LFS objects for {purpose}; install Git LFS "
                    "and verify repository connectivity"
                ) from exc
            if result.returncode != 0:
                raise RuntimeError(
                    f"git lfs fetch failed for {purpose}: {result.stderr[:300]}"
                )

    @staticmethod
    def _parse_lfs_pointer_oid(content: bytes) -> Optional[str]:
        """Return the OID for a canonical LFS pointer, otherwise ``None``."""
        if len(content) > 1024:
            return None
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return None
        lines = text.replace("\r\n", "\n").splitlines()
        if not lines or lines[0] != "version https://git-lfs.github.com/spec/v1":
            return None
        oid_rows = [line[11:] for line in lines if line.startswith("oid sha256:")]
        size_rows = [line[5:] for line in lines if line.startswith("size ")]
        if (
            len(oid_rows) != 1
            or len(size_rows) != 1
            or not re.fullmatch(r"[0-9a-f]{64}", oid_rows[0])
            or not re.fullmatch(r"0|[1-9][0-9]*", size_rows[0])
        ):
            return None
        return oid_rows[0]

    @classmethod
    def _inventory_lfs_oids(
        cls,
        mirror_path: str,
        revisions: list[str],
        audit_env: Mapping[str, str],
    ) -> set[str]:
        """Scan Git objects reachable only from the supplied revision roots."""
        if not revisions:
            return set()
        for revision in revisions:
            if not (
                re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
                or revision.startswith(("refs/heads/", "refs/tags/"))
            ):
                raise RuntimeError("Approved LFS revision inventory is malformed")
        try:
            reachable = subprocess.run(
                [
                    "git", "rev-list", "--objects", "--no-object-names",
                    "--stdin",
                ],
                input="".join(f"{revision}\n" for revision in revisions),
                capture_output=True,
                text=True,
                cwd=mirror_path,
                timeout=600,
                env=dict(audit_env),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Cannot enumerate approved reachable Git objects") from exc
        if reachable.returncode != 0:
            raise RuntimeError(
                "Cannot enumerate approved reachable Git objects: "
                f"{reachable.stderr[:300]}"
            )
        object_ids: list[str] = []
        seen: set[str] = set()
        for line in reachable.stdout.splitlines():
            object_id = line.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
                raise RuntimeError("git rev-list returned a malformed object ID")
            if object_id not in seen:
                seen.add(object_id)
                object_ids.append(object_id)

        candidate_blobs: list[tuple[str, int]] = []
        for offset in range(0, len(object_ids), 10_000):
            batch = object_ids[offset:offset + 10_000]
            checked = subprocess.run(
                [
                    "git", "cat-file",
                    "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                ],
                input="".join(f"{object_id}\n" for object_id in batch),
                capture_output=True,
                text=True,
                cwd=mirror_path,
                timeout=600,
                env=dict(audit_env),
            )
            if checked.returncode != 0:
                raise RuntimeError(
                    "Cannot classify approved reachable Git objects: "
                    f"{checked.stderr[:300]}"
                )
            rows = checked.stdout.splitlines()
            if len(rows) != len(batch):
                raise RuntimeError("git cat-file returned an incomplete object inventory")
            for expected_id, row in zip(batch, rows):
                fields = row.split()
                if len(fields) != 3 or fields[0].lower() != expected_id:
                    raise RuntimeError("git cat-file returned malformed object metadata")
                try:
                    size = int(fields[2])
                except ValueError as exc:
                    raise RuntimeError(
                        "git cat-file returned an invalid object size"
                    ) from exc
                if fields[1] == "blob" and 0 <= size <= 1024:
                    candidate_blobs.append((expected_id, size))

        lfs_oids: set[str] = set()
        for offset in range(0, len(candidate_blobs), 5_000):
            batch = candidate_blobs[offset:offset + 5_000]
            contents = subprocess.run(
                ["git", "cat-file", "--batch"],
                input="".join(f"{object_id}\n" for object_id, _ in batch).encode(),
                capture_output=True,
                cwd=mirror_path,
                timeout=600,
                env=dict(audit_env),
            )
            if contents.returncode != 0:
                stderr = contents.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(
                    "Cannot read approved reachable Git blobs: " + stderr[:300]
                )
            data = contents.stdout
            cursor = 0
            for expected_id, expected_size in batch:
                end = data.find(b"\n", cursor)
                if end < 0:
                    raise RuntimeError("git cat-file returned a truncated blob header")
                header = data[cursor:end].decode("ascii", errors="strict").split()
                cursor = end + 1
                if (
                    len(header) != 3
                    or header[0].lower() != expected_id
                    or header[1] != "blob"
                    or header[2] != str(expected_size)
                    or cursor + expected_size >= len(data)
                ):
                    raise RuntimeError("git cat-file returned malformed blob content")
                content = data[cursor:cursor + expected_size]
                cursor += expected_size
                if data[cursor:cursor + 1] != b"\n":
                    raise RuntimeError("git cat-file returned an invalid blob delimiter")
                cursor += 1
                oid = cls._parse_lfs_pointer_oid(content)
                if oid is not None:
                    lfs_oids.add(oid)
            if cursor != len(data):
                raise RuntimeError("git cat-file returned unexpected trailing data")
        return lfs_oids

    @staticmethod
    def _verify_local_lfs_objects(
        repository_path: str, lfs_oids: set[str], purpose: str
    ) -> None:
        missing: list[str] = []
        corrupt: list[str] = []
        for oid in sorted(lfs_oids):
            object_path = os.path.join(
                repository_path, "lfs", "objects", oid[:2], oid[2:4], oid
            )
            if not os.path.isfile(object_path):
                missing.append(oid)
                continue
            digest = hashlib.sha256()
            with open(object_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != oid:
                corrupt.append(oid)
        if missing or corrupt:
            raise RuntimeError(
                f"{purpose} found {len(missing)} missing and "
                f"{len(corrupt)} corrupt LFS object(s)"
            )

    def _push_lfs_objects(
        self,
        mirror_path: str,
        target_url: str,
        auth_env: dict[str, str],
        repo: Optional[RepoConfig] = None,
        skip_push: bool = False,
        lfs_url: str = "",
        approved_refs: Optional[Mapping[str, str]] = None,
        approved_revisions: Optional[list[str]] = None,
        approved_lfs_oids: Optional[set[str]] = None,
    ) -> dict:
        """Push and independently verify LFS OIDs for approved heads/tags."""
        target_url = self._canonical_git_https_url(
            target_url, "GitHub target clone URL"
        )
        expected_lfs_url = self._lfs_endpoint_for_remote(
            target_url, "GitHub target clone URL"
        )
        lfs_url = self._canonical_git_https_url(
            lfs_url or expected_lfs_url, "GitHub target Git LFS endpoint"
        )
        if not hmac.compare_digest(lfs_url, expected_lfs_url):
            raise RuntimeError(
                "GitHub LFS endpoint is not derived from the approved target"
            )
        refs = self._validated_lfs_ref_inventory(approved_refs)
        approved_ref_names = sorted(refs)
        audit_env = self._credential_free_git_env(auth_env)
        try:
            observed_revisions = self._approved_lfs_revisions(
                mirror_path, refs, audit_env
            )
            if approved_revisions is not None:
                if list(approved_revisions) != observed_revisions:
                    raise RuntimeError(
                        "Approved LFS reachable history changed before target push"
                    )
                revisions = list(approved_revisions)
            else:
                revisions = observed_revisions
            if approved_lfs_oids is None:
                lfs_oids = self._inventory_lfs_oids(
                    mirror_path, revisions, audit_env
                )
            else:
                if not isinstance(approved_lfs_oids, (set, frozenset)) or any(
                    not re.fullmatch(r"[0-9a-f]{64}", str(oid))
                    for oid in approved_lfs_oids
                ):
                    raise RuntimeError("Approved LFS object inventory is malformed")
                lfs_oids = {str(oid) for oid in approved_lfs_oids}
            lfs_count = len(lfs_oids)
            if lfs_count == 0:
                return {
                    "lfs_objects": 0,
                    "lfs_scan": "approved_heads_tags_history",
                    "lfs_verified": True,
                }
            if skip_push:
                raise RuntimeError(
                    f"skip_lfs was approved but {lfs_count} reachable LFS "
                    "object(s) exist; migration cannot be declared complete"
                )

            if repo is not None:
                self._extend_target_fence(repo, 3_660)
                lfs_operation = self._begin_remote_operation(
                    repo,
                    "git_lfs_push",
                    {
                        "target": f"{repo.gh_org}/{repo.gh_repo}",
                        "object_count": lfs_count,
                        "approved_refs_digest": hashlib.sha256(
                            json.dumps(
                                list(refs.items()),
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                        "lfs_oids_digest": hashlib.sha256(
                            "".join(f"{oid}\n" for oid in sorted(lfs_oids)).encode(
                                "ascii"
                            )
                        ).hexdigest(),
                    },
                )
            else:
                lfs_operation = ""
            try:
                for batch in self._chunk_lfs_revisions(approved_ref_names):
                    result = subprocess.run(
                        ["git", "lfs", "push", "--all", "origin", *batch],
                        capture_output=True,
                        text=True,
                        cwd=mirror_path,
                        timeout=3600,
                        env=auth_env,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"git lfs push failed: {result.stderr[:300]}"
                        )
            except subprocess.TimeoutExpired as exc:
                if repo is not None:
                    self._quarantine_target_fence(repo, 0)
                raise RuntimeError(
                    "git lfs push timed out; target quarantined pending reconciliation"
                ) from exc
            except RuntimeError:
                if repo is not None:
                    self._quarantine_target_fence(repo, 0)
                raise
            if repo is not None:
                self._finish_remote_operation(
                    repo,
                    lfs_operation,
                    {
                        "returncode": 0,
                        "object_count": lfs_count,
                        "lfs_oids_digest": hashlib.sha256(
                            "".join(f"{oid}\n" for oid in sorted(lfs_oids)).encode(
                                "ascii"
                            )
                        ).hexdigest(),
                    },
                )
                self._assert_target_fence(repo)
            verification_path = os.path.join(
                os.path.dirname(mirror_path), "target_lfs_verification.git"
            )
            init = subprocess.run(
                ["git", "init", "--bare", verification_path],
                capture_output=True,
                text=True,
                timeout=60,
                env=audit_env,
            )
            if init.returncode != 0:
                raise RuntimeError(
                    f"cannot initialize LFS verification clone: {init.stderr[:300]}"
                )
            add_remote = subprocess.run(
                ["git", "remote", "add", "origin", target_url],
                capture_output=True,
                text=True,
                cwd=verification_path,
                timeout=30,
                env=audit_env,
            )
            if add_remote.returncode != 0:
                raise RuntimeError(
                    f"cannot configure LFS verification remote: "
                    f"{add_remote.stderr[:300]}"
                )
            for approved_ref in approved_ref_names:
                fetch_refs = subprocess.run(
                    [
                        "git", "fetch", "--force", "--no-tags", "origin",
                        f"+{approved_ref}:{approved_ref}",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=verification_path,
                    timeout=3600,
                    env=auth_env,
                )
                if fetch_refs.returncode != 0:
                    raise RuntimeError(
                        f"cannot fetch approved target ref for LFS verification: "
                        f"{fetch_refs.stderr[:300]}"
                    )
            self._fetch_all_lfs_for_approved_refs(
                verification_path,
                "origin",
                approved_ref_names,
                auth_env,
                purpose="target verification history",
            )
            self._verify_local_lfs_objects(
                verification_path, lfs_oids, "Target LFS verification"
            )

            return {
                "lfs_objects": lfs_count,
                "lfs_scan": "approved_heads_tags_history",
                "lfs_push": "success",
                "lfs_verified": True,
            }
        except FileNotFoundError:
            raise RuntimeError(
                "git-lfs is required unless skip_lfs is explicitly approved"
            )

    def _run_gei_migration(self, repo: RepoConfig, source: dict) -> dict:
        """Execute the hash-pinned ADO2GH Enterprise Importer binary.

        GEI is a credential-bearing external process.  It is therefore only
        enabled for the Azure DevOps Services and GitHub Enterprise Cloud
        authorities supported by GitHub's ADO2GH extension.  A standalone,
        plan-bound binary is copied and re-hashed in an isolated directory so
        neither PATH nor GitHub CLI configuration can change the executable or
        destination after approval.
        """
        # GEI performs its own server-side read, so the narrowest enforceable
        # boundary is a complete source recheck immediately before invocation.
        approved_source = self._approved_source_ref_snapshot(repo)
        self._verify_ado_source_snapshot(repo, approved_source)

        source_url = str(
            getattr(self.ado, "org_url", "")
            or self.cfg.get("ado_org_url", "")
        )
        ado_org = self._gei_ado_services_org(source_url)
        target_api_url = self._gei_target_api_url(
            str(getattr(self.gh, "BASE", "") or self.cfg.get("gh_api_url", ""))
        )
        configured_path, expected_sha256 = self._gei_executable_config()

        gei_tmpdir = tempfile.mkdtemp(prefix="ado2gh_gei_")
        try:
            staged_executable = self._stage_verified_gei_executable(
                configured_path, expected_sha256, gei_tmpdir
            )
            cmd = [
                staged_executable, "migrate-repo",
                "--ado-org", ado_org,
                "--ado-team-project", repo.ado_project,
                "--ado-repo", repo.ado_repo,
                "--github-org", repo.gh_org,
                "--github-repo", repo.gh_repo,
                "--wait",
            ]
            if target_api_url != "https://api.github.com":
                cmd.extend(["--target-api-url", target_api_url])

            gh_token = self.gh.token_manager.get_token()
            ado_pat = self.ado.pat
            env = self._isolated_gei_env(gei_tmpdir, ado_pat, gh_token)

            log.info("Running hash-pinned ADO2GH migration %s/%s -> %s/%s",
                     repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo)

            self._extend_target_fence(repo, 7_260)
            gei_operation = self._begin_remote_operation(
                repo,
                "ado2gh_migrate_repo",
                {
                    "source": f"{repo.ado_project}/{repo.ado_repo}",
                    "source_api_url": source_url.rstrip("/"),
                    "target": f"{repo.gh_org}/{repo.gh_repo}",
                    "target_api_url": target_api_url,
                    "ado2gh_executable_sha256": expected_sha256,
                    "approved_source_digest": approved_source.get(
                        "source_refs_digest", ""
                    ),
                },
            )
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=7200, env=env,
                )
            except subprocess.TimeoutExpired as exc:
                self._quarantine_target_fence(repo, 0)
                raise RuntimeError(
                    "ADO2GH migration timed out; target quarantined pending "
                    "reconciliation"
                ) from exc

            stdout = str(result.stdout or "")
            stderr = str(result.stderr or "")
            output_evidence = {
                "stdout_sha256": hashlib.sha256(
                    stdout.encode("utf-8", errors="replace")
                ).hexdigest(),
                "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
                "stderr_sha256": hashlib.sha256(
                    stderr.encode("utf-8", errors="replace")
                ).hexdigest(),
                "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            }
            if result.returncode != 0:
                self._quarantine_target_fence(repo, 0)
                raise RuntimeError(
                    "ADO2GH migration failed with exit "
                    f"{result.returncode}; output was withheld from state/logs "
                    f"(stderr sha256 {output_evidence['stderr_sha256']})"
                )

            self._finish_remote_operation(
                repo,
                gei_operation,
                {
                    "returncode": 0,
                    "operation": "ado2gh_migrate_repo",
                    **output_evidence,
                },
            )

            return {
                "gei": "success",
                "ado2gh_executable_sha256": expected_sha256,
                "target_api_url": target_api_url,
                **output_evidence,
                "requires_review": True,
                "review_reason": (
                    "GEI can migrate pull requests and other non-Git metadata "
                    "that the deterministic ref validator cannot prove; review "
                    "the GEI migration log and target metadata before closing "
                    "this scope"
                ),
            }
        finally:
            shutil.rmtree(gei_tmpdir, ignore_errors=True)

    @staticmethod
    def _gei_ado_services_org(raw_url: str) -> str:
        """Return an exact ADO Services org or reject unsupported authorities."""
        parsed = urlsplit(str(raw_url or "").rstrip("/"))
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.port or 443) != 443
        ):
            raise RuntimeError(
                "GEI requires a credential-free Azure DevOps Services HTTPS URL"
            )
        host = parsed.hostname.casefold()
        path_parts = [unquote(part) for part in parsed.path.split("/") if part]
        if (
            host == "dev.azure.com"
            and len(path_parts) == 1
            and parsed.path == f"/{path_parts[0]}"
        ):
            org = path_parts[0]
        elif (
            host.endswith(".visualstudio.com")
            and not path_parts
            and parsed.path == ""
        ):
            org = host[: -len(".visualstudio.com")]
            if "." in org:
                org = ""
        else:
            org = ""
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?", org):
            raise RuntimeError(
                "GEI only supports exact https://dev.azure.com/{org} or "
                "https://{org}.visualstudio.com Azure DevOps Services authorities"
            )
        return org

    @staticmethod
    def _gei_target_api_url(raw_url: str) -> str:
        """Accept only GitHub.com or GHE.com Enterprise Cloud API authorities."""
        parsed = urlsplit(str(raw_url or "").rstrip("/"))
        host = (parsed.hostname or "").casefold()
        supported_host = host == "api.github.com" or bool(
            re.fullmatch(r"api\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ghe\.com", host)
        )
        if (
            parsed.scheme.casefold() != "https"
            or not supported_host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.port or 443) != 443
        ):
            raise RuntimeError(
                "GEI target must be the exact GitHub.com or GHE.com Enterprise "
                "Cloud base API URL; GitHub Enterprise Server is unsupported"
            )
        return f"https://{host}"

    def _gei_executable_config(self) -> tuple[str, str]:
        configured = self.cfg.get("gei")
        if not isinstance(configured, Mapping):
            raise RuntimeError(
                "GEI requires a plan-bound global.gei executable path and SHA-256"
            )
        path = str(configured.get("ado2gh_executable_path", "")).strip()
        digest = str(configured.get("ado2gh_executable_sha256", "")).strip()
        if not Path(path).is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(
                "GEI executable configuration is missing or not canonical"
            )
        return path, digest

    @staticmethod
    def _stage_verified_gei_executable(
        configured_path: str, expected_sha256: str, tmpdir: str
    ) -> str:
        source = Path(configured_path).resolve(strict=True)
        if not source.is_file():
            raise RuntimeError("Configured ADO2GH executable is not a regular file")
        name = "gh-ado2gh.exe" if os.name == "nt" else "gh-ado2gh"
        staged = Path(tmpdir) / name
        shutil.copy2(source, staged)
        observed = hashlib.sha256(staged.read_bytes()).hexdigest()
        if not hmac.compare_digest(observed, expected_sha256):
            raise RuntimeError(
                "Configured ADO2GH executable SHA-256 does not match the approved plan"
            )
        if os.name != "nt":
            staged.chmod(staged.stat().st_mode | 0o500)
        return str(staged)

    @staticmethod
    def _isolated_gei_env(
        tmpdir: str, ado_pat: str, gh_pat: str
    ) -> dict[str, str]:
        """Create a narrow environment with no inherited GH/ADO routing state."""
        allowed_names = {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TZ",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE",
        }
        env = {
            key: value for key, value in os.environ.items()
            if key in allowed_names
        }
        home = Path(tmpdir) / "home"
        config = Path(tmpdir) / "gh-config"
        home.mkdir(parents=True, exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
        env.update({
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / "xdg"),
            "GH_CONFIG_DIR": str(config),
            "ADO_PAT": str(ado_pat),
            "GH_PAT": str(gh_pat),
        })
        return env

    # ── WORK ITEMS ──────────────────────────────────────────────────────────

    def _migrate_work_items(
        self,
        repo: RepoConfig,
        *,
        _source_payload: Any = None,
        _source_evidence: Optional[dict[str, Any]] = None,
        **_kw: Any,
    ) -> dict:
        log.info("work_items: %s/%s -> %s/%s%s",
                 repo.ado_project, repo.ado_repo,
                 repo.gh_org, repo.gh_repo,
                 " [DRY RUN]" if self.dry_run else "")

        if _source_payload is None:
            _source_payload, _source_evidence = self._fetch_verified_scope_payload(
                repo, MigrationScope.WORK_ITEMS.value
            )
        work_items = _source_payload
        stats = {"total": len(work_items), "created": 0, "skipped": 0,
                 "failed": 0, "source_snapshot": dict(_source_evidence or {})}

        if self.dry_run:
            stats["dry_run"] = True
            return stats

        wi_types = {wi.get("fields", {}).get("System.WorkItemType", "Task")
                    for wi in work_items}
        for label in wi_types:
            target_label = f"ado:{label}"
            self._dispatch_github_mutation(
                repo,
                "github_create_issue_label",
                {"label": target_label},
                lambda target_label=target_label: self.gh.create_label(
                    repo.gh_org, repo.gh_repo, target_label
                ),
            )

        expected_markers = {
            f"<!-- ado2gh:work-item:{repo.ado_project}:{wi.get('id', '')} -->"
            for wi in work_items
        }
        existing_markers: Optional[set[str]] = None
        list_issues = getattr(self.gh, "list_issues", None)
        if callable(list_issues):
            # Read once. Calling the paginated issues endpoint independently
            # for every work item is quadratic and unusable for enterprise
            # projects with tens of thousands of records.
            existing_markers = set()
            for issue in list_issues(repo.gh_org, repo.gh_repo, state="all"):
                if not isinstance(issue, Mapping) or "pull_request" in issue:
                    continue
                for line in str(issue.get("body") or "").splitlines():
                    marker = line.strip()
                    if marker in expected_markers:
                        existing_markers.add(marker)

        for wi in work_items:
            fields = wi.get("fields", {})
            title = fields.get("System.Title", "Untitled")
            wi_type = fields.get("System.WorkItemType", "Task")
            state = fields.get("System.State", "")
            desc = fields.get("System.Description", "") or ""
            body = (
                f"<!-- ado2gh:work-item:{repo.ado_project}:{wi.get('id', '')} -->\n"
                f"**Migrated from Azure DevOps**\n\n"
                f"- **Type:** {wi_type}\n"
                f"- **State:** {state}\n"
                f"- **ADO ID:** {wi.get('id', '')}\n\n"
                f"{desc}"
            )
            try:
                marker = (
                    f"<!-- ado2gh:work-item:{repo.ado_project}:"
                    f"{wi.get('id', '')} -->"
                )
                already_present = (
                    marker in existing_markers
                    if existing_markers is not None
                    else bool(self.gh.find_issue_by_marker(
                        repo.gh_org, repo.gh_repo, marker
                    ))
                )
                if already_present:
                    stats["skipped"] += 1
                    continue
                self._dispatch_github_mutation(
                    repo,
                    "github_create_work_item_issue",
                    {
                        "ado_project": repo.ado_project,
                        "work_item_id": str(wi.get("id", "")),
                        "marker_sha256": hashlib.sha256(
                            marker.encode("utf-8")
                        ).hexdigest(),
                    },
                    lambda title=title, body=body, wi_type=wi_type: (
                        self.gh.create_issue(
                            repo.gh_org,
                            repo.gh_repo,
                            title,
                            body=body,
                            labels=[f"ado:{wi_type}"],
                        )
                    ),
                )
                stats["created"] += 1
                if existing_markers is not None:
                    existing_markers.add(marker)
            except Exception as exc:
                log.warning("work-item %s failed: %s", wi.get("id"), exc)
                stats["failed"] += 1

        if work_items and stats["failed"] == 0:
            stats["requires_review"] = True
            stats["review_reason"] = (
                "ADO work items were projected to GitHub Issues with identity "
                "markers, but comments, attachments, revisions, links, area/iteration "
                "semantics, and user identity require an approved migration policy"
            )
        return stats

    # ── PIPELINES ───────────────────────────────────────────────────────────

    def _get_pipeline_transformer(self):
        if self.transformer is not None:
            return self.transformer
        transformer = getattr(self._pipeline_local, "transformer", None)
        if transformer is None:
            conversion_config = dict(
                self.cfg.get("pipeline_conversion", {}) or {}
            )
            delivery_config = dict(
                self.cfg.get("pipeline_delivery", {}) or {}
            )
            conversion_config["review_staging_branch"] = str(
                delivery_config.get(
                    "branch", "ado2gh/migrated-workflows"
                )
            )
            transformer = create_enterprise_pipeline_transformer_from_env(
                config=conversion_config,
                governance_config=self.cfg.get("governance"),
                governance_plan_id=self._pev_plan_id(),
                governance_run_id=self._pev_run_id(),
                governance_db=self.db,
            )
            self._pipeline_local.transformer = transformer
        return transformer

    @staticmethod
    def _approved_pipeline_artifact_bytes(
        result: Mapping[str, Any], artifact: str
    ) -> bytes:
        """Read a converter artifact from its immutable in-memory handoff."""
        if artifact not in {"workflow", "evidence"}:
            raise ValueError(f"Unknown pipeline artifact kind: {artifact}")
        raw = result.get(f"_approved_{artifact}_bytes")
        expected = str(result.get(f"approved_{artifact}_sha256", ""))
        if not isinstance(raw, bytes) or not expected.startswith("sha256:"):
            raise RuntimeError(
                f"Pipeline converter did not provide approved {artifact} bytes"
            )
        observed = "sha256:" + hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(observed, expected):
            raise RuntimeError(
                f"Approved pipeline {artifact} content digest is inconsistent"
            )
        if artifact == "workflow":
            validation = result.get("validation", {})
            validation_digest = str(
                validation.get("workflow_digest", "")
                if isinstance(validation, Mapping) else ""
            )
            if not validation_digest or not hmac.compare_digest(
                observed, validation_digest
            ):
                raise RuntimeError(
                    "Approved workflow bytes do not match deterministic validation"
                )
        return raw

    def _verify_external_pipeline_configuration(
        self, repo: RepoConfig, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Read back every external resource used to clear a pipeline gate."""
        evidence_bytes = self._approved_pipeline_artifact_bytes(
            result, "evidence"
        )
        try:
            evidence = json.loads(evidence_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("Pipeline approval evidence is malformed") from exc
        approvals = evidence.get("manual_approvals", {}).get("accepted", [])
        if not isinstance(approvals, list):
            raise RuntimeError("Pipeline manual approval evidence is malformed")
        plan = result.get("plan", {})
        ambiguities = {
            str(item.get("ambiguity_id")): item
            for item in plan.get("ambiguities", [])
            if isinstance(item, dict)
        } if isinstance(plan, dict) else {}
        approved_mappings: list[
            tuple[dict[str, Any], dict[str, Any], ManualApprovalRecord]
        ] = []
        for approval in approvals:
            if not isinstance(approval, dict):
                raise RuntimeError("Pipeline approval record is malformed")
            try:
                record = ManualApprovalRecord.from_mapping(approval)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Pipeline approval record failed content verification"
                ) from exc
            target = approval.get("target_mapping", {})
            if isinstance(target, dict):
                ambiguity = ambiguities.get(str(approval.get("ambiguity_id", "")))
                if not isinstance(ambiguity, dict):
                    raise RuntimeError(
                        "Pipeline approval has no exact conversion-plan ambiguity"
                    )
                approved_mappings.append((target, ambiguity, record))
        external = [
            item for item in approved_mappings
            if item[0].get("type") == "external_configuration"
        ]
        runner_labels = sorted({
            str(target.get("runner_label"))
            for target, _ambiguity, _record in approved_mappings
            if target.get("type") == "runner"
        })
        checkout_mappings = [
            (target, ambiguity, record)
            for target, ambiguity, record in approved_mappings
            if target.get("type") == "repository_checkout"
        ]
        workflow_bytes = self._approved_pipeline_artifact_bytes(
            result, "workflow"
        )
        try:
            workflow_text = workflow_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Generated workflow is not valid UTF-8") from exc
        referenced_workflow_secrets = workflow_secret_names(
            workflow_text
        )
        credential_requirements = [
            requirement
            for target, ambiguity, _record in approved_mappings
            for requirement in credential_requirements_for_mapping(
                target, ambiguity
            )
        ]
        if (
            not external
            and not runner_labels
            and not checkout_mappings
            and not referenced_workflow_secrets
        ):
            return {
                "verified": True,
                "external_approval_count": 0,
                "required_secret_names": [],
                "secret_metadata_versions": {},
                "credential_attestation_count": 0,
                "credential_verified_at": "",
                "credential_valid_until": "",
                "target_repository_id": "",
                "runner_labels": [],
                "repository_checkouts": [],
                "environment_configuration_digests": {},
            }

        required_secret_names = sorted({
            str(name)
            for target, _ambiguity, _record in external
            for name in target.get("secret_names", [])
        } | {
            str(target["token_secret"])
            for target, _ambiguity, _record in checkout_mappings
            if target.get("token_secret")
        } | set(referenced_workflow_secrets))

        covered_secret_names = {
            requirement["secret_name"] for requirement in credential_requirements
        }
        uncovered = sorted(set(required_secret_names) - covered_secret_names)
        if uncovered:
            raise RuntimeError(
                "Generated workflow references credential(s) without an "
                "authenticated capability canary: " + ", ".join(uncovered)
            )

        verifier = self._get_credential_attestation_verifier()
        verified_canaries: list[dict[str, Any]] = []
        plan_id = str(plan.get("plan_id", "")) if isinstance(plan, Mapping) else ""
        source_fingerprint = (
            str(plan.get("source_fingerprint", ""))
            if isinstance(plan, Mapping) else ""
        )
        for _target, ambiguity, record in approved_mappings:
            try:
                verified_canaries.extend(
                    verify_record_credential_attestations(
                        record,
                        ambiguity,
                        plan_id=plan_id,
                        source_fingerprint=source_fingerprint,
                        verifier=verifier,
                    )
                )
            except CredentialAttestationError as exc:
                raise RuntimeError(
                    "Pipeline credential canary failed runtime authentication"
                ) from exc
        canary_nonces = [
            str(item["claims"]["nonce"]) for item in verified_canaries
        ]
        canary_run_ids = [
            str(item["claims"]["canary_run_id"])
            for item in verified_canaries
        ]
        if (
            len(set(canary_nonces)) != len(canary_nonces)
            or len(set(canary_run_ids)) != len(canary_run_ids)
        ):
            raise RuntimeError(
                "Pipeline credential canary execution receipt was replayed"
            )

        target_before = self.gh.get_repo(repo.gh_org, repo.gh_repo)
        target_repository_id = (
            self._immutable_repo_id(dict(target_before))
            if isinstance(target_before, Mapping) else ""
        )
        if not target_repository_id:
            raise RuntimeError(
                "GitHub target repository has no immutable identity for "
                "credential attestation"
            )
        for canary in verified_canaries:
            claimed_id = str(canary["claims"]["target_repository_id"])
            if not hmac.compare_digest(
                claimed_id.encode(), target_repository_id.encode()
            ):
                raise RuntimeError(
                    "Pipeline credential canary targets a different immutable repository"
                )

        observed_secret_metadata: list[dict[str, str]] = []
        secret_metadata_by_name: dict[str, dict[str, str]] = {}
        if required_secret_names:
            list_secret_metadata = getattr(
                self.gh, "list_actions_secret_metadata", None
            )
            if not callable(list_secret_metadata):
                raise RuntimeError(
                    "GitHub client cannot verify version-bearing Actions secret metadata"
                )
            observed_secret_metadata = list_secret_metadata(
                repo.gh_org, repo.gh_repo
            )
            if not isinstance(observed_secret_metadata, list) or any(
                not isinstance(item, Mapping)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("updated_at"), str)
                for item in observed_secret_metadata
            ):
                raise RuntimeError(
                    "GitHub Actions secret metadata is malformed"
                )
            secret_metadata_by_name = {
                str(item["name"]): dict(item)
                for item in observed_secret_metadata
            }
            if len(secret_metadata_by_name) != len(observed_secret_metadata):
                raise RuntimeError(
                    "GitHub Actions secret metadata contains duplicate names"
                )
            missing = sorted(
                set(required_secret_names) - set(secret_metadata_by_name)
            )
            if missing:
                raise RuntimeError(
                    "Approved pipeline external configuration references missing "
                    "GitHub Actions secret name(s): " + ", ".join(missing)
                )
            for canary in verified_canaries:
                claims = canary["claims"]
                secret_name = str(claims["secret_name"])
                live_version = str(
                    secret_metadata_by_name[secret_name]["updated_at"]
                )
                claimed_version = str(claims["secret_updated_at"])
                if not hmac.compare_digest(
                    live_version.encode(), claimed_version.encode()
                ):
                    raise RuntimeError(
                        f"GitHub Actions secret {secret_name!r} was rotated after "
                        "credential attestation"
                    )

        environments: dict[str, str] = {}
        get_environment = getattr(self.gh, "get_environment", None)
        for target, ambiguity, _record in external:
            if ambiguity.get("kind") != "environment_protection":
                continue
            if not callable(get_environment):
                raise RuntimeError(
                    "GitHub client cannot verify approved environment protection"
                )
            name = str(target.get("resource", ""))
            live = get_environment(repo.gh_org, repo.gh_repo, name)
            if not isinstance(live, Mapping):
                raise RuntimeError(
                    f"Approved GitHub environment {name!r} does not exist"
                )
            observed_digest = github_environment_configuration_digest(live)
            expected_digest = str(target.get("configuration_digest", ""))
            if not hmac.compare_digest(observed_digest, expected_digest):
                raise RuntimeError(
                    f"GitHub environment {name!r} protection does not match "
                    "its content-addressed approval"
                )
            environments[name] = observed_digest

        if runner_labels:
            list_runners = getattr(self.gh, "list_actions_runners", None)
            if not callable(list_runners):
                raise RuntimeError(
                    "GitHub client cannot verify approved runner labels"
                )
            runners = list_runners(repo.gh_org, repo.gh_repo)
            available_labels = {
                str(label.get("name"))
                for runner in runners if runner.get("status") == "online"
                for label in runner.get("labels", [])
                if isinstance(label, Mapping) and label.get("name")
            }
            missing_runner_labels = sorted(
                set(runner_labels) - available_labels
            )
            if missing_runner_labels:
                raise RuntimeError(
                    "Approved GitHub runner label(s) have no online runner: "
                    + ", ".join(missing_runner_labels)
                )

        repository_checkouts: list[dict[str, str]] = []
        resolve_commit = getattr(self.gh, "get_commit_sha", None)
        checkout_canaries = {
            str(canary["claims"]["ambiguity_id"]): canary["claims"]
            for canary in verified_canaries
            if canary["claims"]["capability"]
            == "external_repository_checkout"
        }
        for target, ambiguity, _record in checkout_mappings:
            slug = str(target.get("repository", ""))
            owner, name = slug.split("/", 1)
            checkout_repo = self.gh.get_repo(owner, name)
            checkout_repository_id = (
                self._immutable_repo_id(dict(checkout_repo))
                if isinstance(checkout_repo, Mapping) else ""
            )
            if not checkout_repository_id:
                raise RuntimeError(
                    f"Approved checkout repository {slug!r} is unavailable"
                )
            ref = str(target.get("ref", ""))
            resolved_sha = ""
            if ref:
                if not callable(resolve_commit):
                    raise RuntimeError(
                        "GitHub client cannot resolve approved checkout refs"
                    )
                resolved_sha = resolve_commit(owner, name, ref)
                if not re.fullmatch(r"[0-9a-fA-F]{40}", resolved_sha):
                    raise RuntimeError(
                        f"Approved checkout ref {slug}@{ref} is invalid"
                    )
            claims = checkout_canaries.get(str(ambiguity.get("ambiguity_id", "")))
            if not isinstance(claims, Mapping):
                raise RuntimeError(
                    f"Approved checkout repository {slug!r} has no trusted canary"
                )
            for field, live_value in (
                ("external_repository_id", checkout_repository_id),
                ("external_ref", ref),
                ("external_commit_sha", resolved_sha.lower()),
            ):
                if not hmac.compare_digest(
                    str(claims.get(field, "")).encode(), live_value.encode()
                ):
                    raise RuntimeError(
                        f"Approved checkout repository {slug!r} {field} drifted "
                        "from its credential canary"
                    )
            repository_checkouts.append({
                "repository": slug,
                "target_repo_id": checkout_repository_id,
                "ref": ref,
                "resolved_sha": resolved_sha.lower(),
            })

        # A concurrent secret/environment change invalidates the read set.
        if required_secret_names and list_secret_metadata(
            repo.gh_org, repo.gh_repo
        ) != observed_secret_metadata:
            raise RuntimeError(
                "GitHub Actions secret metadata changed during approval readback"
            )
        for name, expected_digest in environments.items():
            live = get_environment(repo.gh_org, repo.gh_repo, name)
            if not isinstance(live, Mapping) or not hmac.compare_digest(
                github_environment_configuration_digest(live), expected_digest
            ):
                raise RuntimeError(
                    f"GitHub environment {name!r} changed during approval readback"
                )
        for checkout in repository_checkouts:
            owner, name = checkout["repository"].split("/", 1)
            live_checkout = self.gh.get_repo(owner, name)
            live_checkout_id = (
                self._immutable_repo_id(dict(live_checkout))
                if isinstance(live_checkout, Mapping) else ""
            )
            live_checkout_sha = resolve_commit(
                owner, name, checkout["ref"]
            ).lower()
            if (
                not live_checkout_id
                or not hmac.compare_digest(
                    live_checkout_id.encode(),
                    checkout["target_repo_id"].encode(),
                )
                or not hmac.compare_digest(
                    live_checkout_sha.encode(), checkout["resolved_sha"].encode()
                )
            ):
                raise RuntimeError(
                    f"Approved checkout {checkout['repository']!r} changed "
                    "during credential readback"
                )
        target_after = self.gh.get_repo(repo.gh_org, repo.gh_repo)
        target_after_id = (
            self._immutable_repo_id(dict(target_after))
            if isinstance(target_after, Mapping) else ""
        )
        if not target_after_id or not hmac.compare_digest(
            target_after_id.encode(), target_repository_id.encode()
        ):
            raise RuntimeError(
                "GitHub target repository identity changed during credential readback"
            )
        return {
            "verified": True,
            "external_approval_count": len(external),
            "required_secret_names": required_secret_names,
            "secret_metadata_versions": {
                name: str(secret_metadata_by_name[name]["updated_at"])
                for name in required_secret_names
            },
            "credential_attestation_count": len(verified_canaries),
            "credential_verified_at": (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                if verified_canaries else ""
            ),
            "credential_valid_until": (
                min(
                    str(canary["claims"]["expires_at"])
                    for canary in verified_canaries
                ) if verified_canaries else ""
            ),
            "target_repository_id": target_repository_id,
            "runner_labels": runner_labels,
            "repository_checkouts": repository_checkouts,
            "environment_configuration_digests": environments,
        }

    def _migrate_pipelines(self, repo: RepoConfig, *,
                           pipeline_parallel: int = 8,
                           wave_id: int = 0, **_kw: Any) -> dict:
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        snapshot = self.pipeline_snapshots.get(source_key)
        if self.cfg.get("pev_plan_id") and snapshot is None:
            raise RuntimeError(
                f"Approved pipeline snapshot is missing for {source_key}"
            )
        if snapshot is not None:
            if (
                snapshot.project != repo.ado_project
                or snapshot.repo_name != repo.ado_repo
            ):
                raise RuntimeError(
                    f"Approved pipeline snapshot identity mismatch for {source_key}"
                )
            pipelines = list(snapshot.pipelines())
            approved_receipts = {
                (int(item["pipeline_id"]), str(item["pipeline_type"])): item
                for item in snapshot.receipts()
            }
            if len(approved_receipts) != snapshot.pipeline_count:
                raise RuntimeError(
                    f"Approved pipeline snapshot has duplicate identities for {source_key}"
                )
        else:
            pipelines = self.db.get_pipelines_for_repo(
                repo.ado_project, repo.ado_repo
            )
            approved_receipts = None
        if repo.pipeline_filter:
            pat = re.compile(repo.pipeline_filter)
            pipelines = [p for p in pipelines if pat.search(p.pipeline_name)]

        stats: dict[str, Any] = {
            "total": len(pipelines), "completed": 0,
            "failed": 0, "needs_review": 0, "skipped": 0, "warnings": [],
        }

        if not pipelines:
            stats["production_ready"] = True
            stats["delivery"] = {"state": "not_required", "remote_verified": True}
            return stats

        # Pre-create GitHub environments
        env_names: set[str] = set()
        protected_env_names: set[str] = set()
        environment_requirements: dict[str, dict[str, int]] = {}

        def _record_environment(environment: Any) -> None:
            if environment is None:
                return
            name = str(getattr(environment, "name", "")).strip()
            if not name:
                return
            env_names.add(name)
            approvers = len(getattr(environment, "required_approvers", []))
            checks = (
                len(getattr(environment, "pre_deploy_checks", []))
                + len(getattr(environment, "post_deploy_checks", []))
            )
            requirements = environment_requirements.setdefault(
                name, {"approvers": 0, "checks": 0}
            )
            requirements["approvers"] = max(
                requirements["approvers"], approvers
            )
            requirements["checks"] = max(requirements["checks"], checks)
            if approvers or checks:
                protected_env_names.add(name)

        for pipe in pipelines:
            for env in pipe.environments:
                _record_environment(env)
            for stage in pipe.stages:
                if stage.environment:
                    _record_environment(stage.environment)

        if not self.dry_run:
            for env_name in sorted(env_names):
                try:
                    requirements = environment_requirements.get(
                        env_name, {"approvers": 0, "checks": 0}
                    )
                    self._ensure_github_environment(
                        repo,
                        env_name,
                        require_existing=env_name in protected_env_names,
                        required_approver_count=requirements["approvers"],
                        required_check_count=requirements["checks"],
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"GitHub environment {env_name!r} could not be created"
                    ) from exc

        pev_plan_id = str(self.cfg.get("pev_plan_id", ""))
        pev_run_id = str(self.cfg.get("pev_run_id", ""))
        existing = self.db.get_wave_pipeline_migrations(
            wave_id,
            pev_plan_id=pev_plan_id or None,
            pev_run_id=pev_run_id or None,
        )

        def _stored_stats(row: dict[str, Any]) -> dict[str, Any]:
            raw = row.get("transform_stats") or "{}"
            if isinstance(raw, dict):
                return raw
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                return {}

        def _stored_credential_attestation_count(
            row: dict[str, Any]
        ) -> int:
            external = _stored_stats(row).get(
                "external_configuration_evidence", {}
            )
            if not isinstance(external, Mapping):
                return 1
            value = external.get("credential_attestation_count", 0)
            return value if isinstance(value, int) and not isinstance(
                value, bool
            ) and value >= 0 else 1

        existing_by_identity = {
            (int(r["pipeline_id"]), str(r.get("pipeline_type", "yaml"))): r
            for r in existing
            if r.get("project") == repo.ado_project
            and r.get("repo_name") == repo.ado_repo
            and r.get("gh_org") == repo.gh_org
            and r.get("gh_repo") == repo.gh_repo
            and (
                not pev_plan_id
                or (
                    r.get("pev_plan_id") == pev_plan_id
                    and r.get("pev_run_id") == pev_run_id
                )
            )
        }
        pending = list(pipelines)

        output_root = (output_base() / "workflows" / repo.gh_org
                       / repo.gh_repo / ".github" / "workflows")

        conversion_planner = PipelineConversionPlanner()
        source_fingerprints: dict[tuple[int, str], str] = {}
        approved_artifact_contents: dict[str, bytes] = {}
        approved_artifact_lock = threading.Lock()

        def _approved_receipt_digest(identity: tuple[int, str]) -> str:
            approved_receipt = (
                approved_receipts.get(identity) if approved_receipts is not None
                else None
            )
            if approved_receipt is None:
                return ""
            return hashlib.sha256(
                json.dumps(
                    approved_receipt,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()

        def _audit_result(
            attempt_id: str,
            result: dict[str, Any],
            status: str,
            error: str = "",
        ) -> None:
            self._assert_target_fence(repo)
            evidence_ref = str(result.get("evidence_file", "")).strip()
            evidence_path = Path(evidence_ref) if evidence_ref else None
            evidence: dict[str, Any] = {}
            if evidence_path is None:
                raise FileNotFoundError(
                    "Pipeline conversion did not produce its required audit evidence"
                )
            evidence_bytes = self._approved_pipeline_artifact_bytes(
                result, "evidence"
            )
            try:
                loaded = json.loads(evidence_bytes.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(
                    "Pipeline evidence is not valid UTF-8 JSON"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError("Pipeline evidence root must be an object")
            evidence = loaded
            llm = evidence.get("llm", {}) if isinstance(evidence, dict) else {}
            resolutions = llm.get("resolutions", []) if isinstance(llm, dict) else []
            if resolutions:
                raise RuntimeError(
                    "Pipeline evidence contains directly executable LLM "
                    "resolutions; only proposal-only evidence is accepted"
                )
            proposals = llm.get("proposals", []) if isinstance(llm, dict) else []
            if not isinstance(proposals, list):
                raise ValueError("Pipeline LLM proposals evidence must be a list")
            response_digests = sorted(
                str(item.get("response_digest", ""))
                for item in proposals if isinstance(item, dict)
                and item.get("response_digest")
            )
            aggregate_digest = (
                "sha256:" + hashlib.sha256(
                    "\n".join(response_digests).encode("utf-8")
                ).hexdigest()
                if response_digests else ""
            )
            validation = result.get("validation", {})
            production_ready = bool(result.get("production_ready", False))
            if pev_run_id:
                for resolution in proposals:
                    if not isinstance(resolution, dict):
                        continue
                    prompt_digest = str(resolution.get("prompt_digest") or "")
                    response_digest = str(resolution.get("response_digest") or "")
                    if not prompt_digest or not response_digest:
                        raise RuntimeError(
                            "Accepted LLM resolution is missing redacted audit digests"
                        )
                    self.db.record_llm_decision(
                        pev_run_id,
                        provider=str(llm.get("provider") or "unknown"),
                        model=str(resolution.get("model") or llm.get("model") or "unknown"),
                        prompt_digest=prompt_digest,
                        response_digest=response_digest,
                        reason=str(resolution.get("ambiguity_id") or "pipeline ambiguity"),
                        confidence=float(resolution.get("confidence", 0)),
                        schema_valid=True,
                        policy_status="proposal_only",
                        artifact_ref=str(evidence_path or ""),
                    )
            if not self.db.finish_pipeline_conversion_attempt(
                attempt_id,
                status,
                response_digest=aggregate_digest,
                resolution_count=0,
                validation_status=str(
                    validation.get("status", "failed")
                    if isinstance(validation, dict) else "failed"
                ),
                production_ready=production_ready,
                validation_report=validation if isinstance(validation, dict) else {},
                evidence_file=str(evidence_path or ""),
                error=error or None,
            ):
                raise RuntimeError(f"Could not finalize conversion attempt {attempt_id}")

        def _result_stats(
            result: dict[str, Any], pipe: PipelineMetadata
        ) -> dict[str, Any]:
            plan = result.get("plan", {})
            workflow_path = Path(str(result.get("workflow_file", "")))
            content = self._approved_pipeline_artifact_bytes(
                result, "workflow"
            )
            workflow_blob_sha = hashlib.sha1(
                f"blob {len(content)}\0".encode("ascii") + content
            ).hexdigest()
            evidence_path = Path(str(result.get("evidence_file", "")))
            evidence_content = self._approved_pipeline_artifact_bytes(
                result, "evidence"
            )
            evidence_blob_sha = hashlib.sha1(
                f"blob {len(evidence_content)}\0".encode("ascii")
                + evidence_content
            ).hexdigest()
            identity = (pipe.pipeline_id, pipe.pipeline_type.value)
            return {
                **dict(result.get("stats") or {}),
                "production_ready": bool(result.get("production_ready", False)),
                "conversion_status": str(result.get("conversion_status", "failed")),
                "evidence_file": str(result.get("evidence_file", "")),
                "conversion_plan_id": str(
                    plan.get("plan_id", "") if isinstance(plan, dict) else ""
                ),
                "conversion_source_fingerprint": str(
                    plan.get("source_fingerprint", "")
                    if isinstance(plan, dict) else ""
                ),
                "llm_used": bool(result.get("llm_used", False)),
                "external_configuration_evidence": dict(
                    result.get("external_configuration_evidence") or {}
                ),
                "workflow_blob_sha": workflow_blob_sha,
                "evidence_blob_sha": evidence_blob_sha,
                "approved_workflow_sha256": str(
                    result.get("approved_workflow_sha256", "")
                ),
                "approved_evidence_sha256": str(
                    result.get("approved_evidence_sha256", "")
                ),
                "evidence_remote_path": (
                    f".ado2gh/pipeline-evidence/{evidence_path.name}"
                    if evidence_path.name else ""
                ),
                "approved_pipeline_receipt_digest": _approved_receipt_digest(
                    identity
                ),
                "approved_inventory_digest": (
                    snapshot.inventory_digest if snapshot is not None else ""
                ),
            }

        def _transform_one(pipe: PipelineMetadata) -> dict:
            attempt_id = ""
            source_fingerprint = ""
            try:
                # Establish a normalized-metadata fingerprint even when the
                # just-in-time YAML read subsequently fails. Successful YAML
                # reads replace this with the full conversion fingerprint.
                source_fingerprint = conversion_planner.plan(
                    pipe
                ).source_fingerprint
                # Raw YAML is intentionally not persisted in SQLite because it
                # may contain inline credentials. Rehydrate it just-in-time
                # over the authenticated source connection.
                if pipe.pipeline_type.value == "yaml":
                    source_yaml = self.ado.get_pipeline_yaml_from_git(
                        pipe.project,
                        pipe.repo_id,
                        pipe.yaml_path,
                        branch=pipe.repo_branch.replace("refs/heads/", ""),
                    )
                    if isinstance(source_yaml, bytes):
                        source_yaml_bytes = source_yaml
                        try:
                            source_yaml_text = source_yaml.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise RuntimeError(
                                f"Source YAML is not valid UTF-8 for pipeline "
                                f"{pipe.project}/{pipe.pipeline_name}"
                            ) from exc
                    elif isinstance(source_yaml, str):
                        source_yaml_text = source_yaml
                        source_yaml_bytes = source_yaml.encode("utf-8")
                    else:
                        source_yaml_text = ""
                        source_yaml_bytes = b""
                    if not source_yaml_text.strip():
                        raise RuntimeError(
                            f"Source YAML is unavailable for pipeline "
                            f"{pipe.project}/{pipe.pipeline_name} at {pipe.yaml_path}"
                        )
                    if approved_receipts is not None:
                        receipt = approved_receipts.get(
                            (pipe.pipeline_id, pipe.pipeline_type.value)
                        )
                        expected_sha = str(
                            (receipt or {}).get("source_yaml_sha256", "")
                        )
                        observed_sha = hashlib.sha256(
                            source_yaml_bytes
                        ).hexdigest()
                        if not expected_sha or not hmac.compare_digest(
                            observed_sha, expected_sha
                        ):
                            raise RuntimeError(
                                f"Source YAML drift for pipeline "
                                f"{pipe.project}/{pipe.pipeline_name} at "
                                f"{pipe.yaml_path}: approved content is no "
                                f"longer available"
                            )
                    pipe.yaml_content = source_yaml_text
                conversion_plan = conversion_planner.plan(pipe)
                source_fingerprint = conversion_plan.source_fingerprint
                identity = (pipe.pipeline_id, pipe.pipeline_type.value)
                source_fingerprints[identity] = source_fingerprint
                prior = existing_by_identity.get(identity)
                if (
                    prior
                    and prior.get("status") == MigrationStatus.COMPLETED.value
                    and prior.get("source_fingerprint") == source_fingerprint
                    and bool(_stored_stats(prior).get("production_ready", False))
                    and _stored_stats(prior).get(
                        "conversion_source_fingerprint"
                    ) == source_fingerprint
                    and _stored_stats(prior).get("ruleset_version")
                    == conversion_plan.ruleset_version
                    and bool(_stored_stats(prior).get("workflow_blob_sha"))
                    and bool(_stored_stats(prior).get("evidence_blob_sha"))
                    and str(_stored_stats(prior).get(
                        "approved_workflow_sha256", ""
                    )).startswith("sha256:")
                    and str(_stored_stats(prior).get(
                        "approved_evidence_sha256", ""
                    )).startswith("sha256:")
                    # Credential canaries are deliberately short-lived and
                    # must be authenticated against live secret/ref metadata
                    # on every execution. A prior conversion receipt can
                    # never be used to skip that runtime boundary.
                    and _stored_credential_attestation_count(prior) == 0
                    and bool(prior.get("workflow_file"))
                    and (
                        approved_receipts is None
                        or (
                            _stored_stats(prior).get("approved_inventory_digest")
                            == snapshot.inventory_digest
                            and _stored_stats(prior).get(
                                "approved_pipeline_receipt_digest"
                            ) == _approved_receipt_digest(identity)
                        )
                    )
                ):
                    return {
                        "pipeline_id": pipe.pipeline_id,
                        "pipeline_type": pipe.pipeline_type.value,
                        "status": "skipped",
                    }
                self._assert_target_fence(repo)
                self.db.upsert_pipeline_migration(
                    wave_id,
                    pipe,
                    repo.gh_org,
                    repo.gh_repo,
                    MigrationStatus.IN_PROGRESS,
                    pev_plan_id=pev_plan_id,
                    pev_run_id=pev_run_id,
                    source_fingerprint=source_fingerprint,
                )
                llm_settings = dict(
                    self.cfg.get("pipeline_conversion", {}) or {}
                )
                provider = str(
                    llm_settings.get("llm_provider", "disabled")
                ).strip()
                model = str(llm_settings.get("llm_model", "")).strip()
                attempt_id = self.db.start_pipeline_conversion_attempt(
                    wave_id,
                    pipe.project,
                    pipe.pipeline_id,
                    conversion_plan.source_fingerprint,
                    pipeline_type=pipe.pipeline_type.value,
                    plan_id=conversion_plan.plan_id,
                    ruleset_version=conversion_plan.ruleset_version,
                    mode=conversion_plan.mode.value,
                    llm_used=bool(
                        conversion_plan.llm_ambiguities
                        and provider not in {"", "disabled"}
                    ),
                    provider=provider,
                    model=model,
                    correlation_id=pev_run_id,
                )
                result = self._get_pipeline_transformer().transform(pipe, output_root)
                if not bool(result.get("production_ready", False)):
                    raise RuntimeError(
                        "Pipeline converter returned a non-production-ready artifact"
                    )
                result["external_configuration_evidence"] = (
                    self._verify_external_pipeline_configuration(repo, result)
                )
                _audit_result(attempt_id, result, "completed")
                self._assert_target_fence(repo)
                transform_stats = _result_stats(result, pipe)
                self.db.upsert_pipeline_migration(
                    wave_id, pipe, repo.gh_org, repo.gh_repo,
                    MigrationStatus.COMPLETED,
                    workflow_file=str(result.get("workflow_file", "")),
                    warnings=result.get("warnings", []),
                    unsupported=result.get("unsupported_tasks", []),
                    transform_stats=transform_stats,
                    pev_plan_id=pev_plan_id,
                    pev_run_id=pev_run_id,
                    source_fingerprint=source_fingerprint,
                )
                with approved_artifact_lock:
                    approved_artifact_contents[
                        str(Path(str(result["workflow_file"])).resolve())
                    ] = self._approved_pipeline_artifact_bytes(
                        result, "workflow"
                    )
                    approved_artifact_contents[
                        str(Path(str(result["evidence_file"])).resolve())
                    ] = self._approved_pipeline_artifact_bytes(
                        result, "evidence"
                    )
                return {
                    "pipeline_id": pipe.pipeline_id,
                    "pipeline_type": pipe.pipeline_type.value,
                    "status": "completed",
                }
            except PipelineValidationError as exc:
                result = dict(exc.result or {})
                result.setdefault("validation", exc.report.to_dict())
                result.setdefault("production_ready", False)
                result.setdefault("evidence_file", exc.evidence_file)
                review_only = bool(exc.report.valid and not exc.report.production_ready)
                item_status = "needs_review" if review_only else "failed"
                migration_status = (
                    MigrationStatus.NEEDS_REVIEW if review_only
                    else MigrationStatus.FAILED
                )
                audit_error = str(exc)
                try:
                    if attempt_id:
                        _audit_result(attempt_id, result, item_status, audit_error)
                except Exception as audit_exc:
                    item_status = "failed"
                    migration_status = MigrationStatus.FAILED
                    audit_error = f"{audit_error}; audit persistence failed: {audit_exc}"
                    if attempt_id:
                        try:
                            self.db.finish_pipeline_conversion_attempt(
                                attempt_id,
                                "failed",
                                validation_status="failed",
                                production_ready=False,
                                error=audit_error,
                            )
                        except Exception:
                            log.exception(
                                "Could not close failed pipeline audit attempt %s",
                                attempt_id,
                            )
                self._assert_target_fence(repo)
                self.db.upsert_pipeline_migration(
                    wave_id, pipe, repo.gh_org, repo.gh_repo,
                    migration_status,
                    workflow_file=str(result.get("workflow_file", "")),
                    error=audit_error,
                    warnings=result.get("warnings", []),
                    unsupported=result.get("unsupported_tasks", []),
                    transform_stats=_result_stats(result, pipe),
                    pev_plan_id=pev_plan_id,
                    pev_run_id=pev_run_id,
                    source_fingerprint=source_fingerprint,
                )
                return {
                    "pipeline_id": pipe.pipeline_id,
                    "pipeline_type": pipe.pipeline_type.value,
                    "status": item_status,
                    "error": audit_error,
                }
            except Exception as exc:
                if attempt_id:
                    try:
                        self.db.finish_pipeline_conversion_attempt(
                            attempt_id,
                            "failed",
                            validation_status="failed",
                            production_ready=False,
                            error=str(exc),
                        )
                    except Exception as audit_exc:
                        log.error(
                            "conversion attempt %s could not be finalized: %s",
                            attempt_id, audit_exc,
                        )
                self._assert_target_fence(repo)
                self.db.upsert_pipeline_migration(
                    wave_id, pipe, repo.gh_org, repo.gh_repo,
                    MigrationStatus.FAILED,
                    error=str(exc),
                    pev_plan_id=pev_plan_id,
                    pev_run_id=pev_run_id,
                    source_fingerprint=source_fingerprint,
                )
                return {
                    "pipeline_id": pipe.pipeline_id,
                    "pipeline_type": pipe.pipeline_type.value,
                    "status": "failed", "error": str(exc),
                }

        with ThreadPoolExecutor(max_workers=min(pipeline_parallel, max(1, len(pending)))) as pool:
            futures = {pool.submit(_transform_one, p): p for p in pending}
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                except Exception as exc:
                    pipe = futures[fut]
                    res = {
                        "pipeline_id": pipe.pipeline_id,
                        "pipeline_type": pipe.pipeline_type.value,
                        "status": "failed",
                        "error": f"Unhandled conversion worker failure: {exc}",
                    }
                if res["status"] == "completed":
                    stats["completed"] += 1
                elif res["status"] == "skipped":
                    stats["skipped"] += 1
                elif res["status"] == "needs_review":
                    stats["needs_review"] += 1
                    stats["warnings"].append(res.get("error", "review required"))
                else:
                    stats["failed"] += 1
                    stats["warnings"].append(res.get("error", "unknown"))

        if stats["failed"]:
            stats["production_ready"] = False
            stats["delivery"] = {"state": "blocked_conversion", "remote_verified": False}
            return stats
        if stats["needs_review"]:
            stats["production_ready"] = False
            stats["requires_review"] = True
            stats["review_reason"] = (
                f"{stats['needs_review']} pipeline conversion(s) require approved "
                "external mappings or human review"
            )
            stats["delivery"] = {"state": "blocked_review", "remote_verified": False}
            return stats

        if self.dry_run:
            # A PEV preview performs the deterministic transform, optional
            # bounded LLM resolution, and local validator.  It stops before
            # environments, branches, files, or pull requests are written.
            stats["dry_run"] = True
            stats["production_ready"] = True
            stats["delivery"] = {
                "state": "preview_validated",
                "remote_verified": False,
            }
            return stats

        expected = {
            (pipe.pipeline_id, pipe.pipeline_type.value) for pipe in pipelines
        }
        current_rows = [
            row for row in self.db.get_wave_pipeline_migrations(
                wave_id,
                pev_plan_id=pev_plan_id or None,
                pev_run_id=pev_run_id or None,
            )
            if row.get("project") == repo.ado_project
            and row.get("repo_name") == repo.ado_repo
            and row.get("gh_org") == repo.gh_org
            and row.get("gh_repo") == repo.gh_repo
            and (int(row.get("pipeline_id", -1)), row.get("pipeline_type")) in expected
            and (
                not pev_plan_id
                or (
                    row.get("pev_plan_id") == pev_plan_id
                    and row.get("pev_run_id") == pev_run_id
                    and row.get("source_fingerprint") == source_fingerprints.get((
                        int(row.get("pipeline_id", -1)),
                        str(row.get("pipeline_type", "")),
                    ))
                    and _stored_stats(row).get(
                        "conversion_source_fingerprint"
                    ) == row.get("source_fingerprint")
                    and _stored_stats(row).get("approved_inventory_digest")
                    == (snapshot.inventory_digest if snapshot is not None else "")
                    and _stored_stats(row).get(
                        "approved_pipeline_receipt_digest"
                    ) == _approved_receipt_digest((
                        int(row.get("pipeline_id", -1)),
                        str(row.get("pipeline_type", "")),
                    ))
                )
            )
            and row.get("status") == MigrationStatus.COMPLETED.value
            and bool(_stored_stats(row).get("production_ready", False))
        ]
        if len(current_rows) != len(expected):
            stats["failed"] += 1
            stats["production_ready"] = False
            stats["warnings"].append(
                "Not every inventoried pipeline has a production-ready conversion receipt"
            )
            stats["delivery"] = {"state": "blocked_receipts", "remote_verified": False}
            return stats

        workflow_files = [Path(str(row["workflow_file"])) for row in current_rows]
        evidence_files = [
            Path(str(_stored_stats(row).get("evidence_file")))
            for row in current_rows if _stored_stats(row).get("evidence_file")
        ]

        # Rehydrate an immutable content map.  Fresh conversions contribute
        # the exact bytes returned by the validator.  A resumed run may read a
        # local artifact once, but only when both its persisted SHA-256 and Git
        # blob ID match the completed conversion receipt.
        def _bind_row_artifact(
            path: Path,
            row_stats: Mapping[str, Any],
            artifact: str,
        ) -> None:
            resolved = str(path.resolve())
            with approved_artifact_lock:
                content = approved_artifact_contents.get(resolved)
            if content is None:
                if not path.is_file():
                    raise FileNotFoundError(
                        f"Approved pipeline {artifact} artifact is unavailable: {path}"
                    )
                content = path.read_bytes()
            expected_blob = str(row_stats.get(f"{artifact}_blob_sha", ""))
            expected_sha256 = str(
                row_stats.get(f"approved_{artifact}_sha256", "")
            )
            observed_blob = hashlib.sha1(
                f"blob {len(content)}\0".encode("ascii") + content
            ).hexdigest()
            observed_sha256 = "sha256:" + hashlib.sha256(content).hexdigest()
            if (
                not expected_blob
                or not expected_sha256.startswith("sha256:")
                or not hmac.compare_digest(observed_blob, expected_blob)
                or not hmac.compare_digest(observed_sha256, expected_sha256)
            ):
                raise RuntimeError(
                    f"Approved pipeline {artifact} artifact changed after validation: {path}"
                )
            with approved_artifact_lock:
                prior = approved_artifact_contents.setdefault(resolved, content)
                if not hmac.compare_digest(prior, content):
                    raise RuntimeError(
                        f"Conflicting approved bytes for pipeline artifact: {path}"
                    )

        for row in current_rows:
            row_stats = _stored_stats(row)
            workflow_path = Path(str(row["workflow_file"]))
            evidence_path = Path(str(row_stats.get("evidence_file", "")))
            _bind_row_artifact(workflow_path, row_stats, "workflow")
            if not str(row_stats.get("evidence_file", "")).strip():
                raise RuntimeError(
                    "Completed pipeline receipt has no approved evidence artifact"
                )
            _bind_row_artifact(evidence_path, row_stats, "evidence")

        delivery_cfg = dict(self.cfg.get("pipeline_delivery", {}))
        mode = delivery_cfg.get("mode", "pull_request")
        if mode == "local":
            stats["delivery"] = {"state": "local_only", "remote_verified": False}
            stats["production_ready"] = False
            stats["requires_review"] = True
            stats["review_reason"] = (
                "Validated workflows exist only as local artifacts; production "
                "completion requires target default-branch verification"
            )
            return stats

        publisher = WorkflowPublisher(
            self.gh,
            branch=str(delivery_cfg.get("branch", "ado2gh/migrated-workflows")),
            mutation_runner=self._dispatch_github_mutation,
        )
        delivery = publisher.publish(
            repo,
            workflow_files,
            evidence_files,
            title=str(
                delivery_cfg.get(
                    "title", "Review migrated GitHub Actions workflows"
                )
            ),
            approved_contents=approved_artifact_contents,
        )
        self._assert_target_write_boundary(repo)
        stats["delivery"] = delivery
        stats["production_ready"] = bool(delivery.get("remote_verified", False))
        if not stats["production_ready"]:
            stats["requires_review"] = True
            stats["review_reason"] = (
                "Workflow conversion passed, but its review PR must be merged "
                "and verified on the target default branch"
            )
        return stats

    # ── WIKI ────────────────────────────────────────────────────────────────

    def _migrate_wiki(
        self,
        repo: RepoConfig,
        *,
        _source_payload: Any = None,
        _source_evidence: Optional[dict[str, Any]] = None,
        **_kw: Any,
    ) -> dict:
        if _source_payload is None:
            _source_payload, _source_evidence = self._fetch_verified_scope_payload(
                repo, MigrationScope.WIKI.value
            )
        wikis = _source_payload
        stats = {
            "wiki_count": len(wikis),
            "pages": 0,
            "source_snapshot": dict(_source_evidence or {}),
        }
        if self.dry_run:
            stats["dry_run"] = True
            return stats
        out = output_base() / "wikis" / repo.gh_org / repo.gh_repo
        out.mkdir(parents=True, exist_ok=True)
        for wiki_data in wikis:
            root = wiki_data.get("root", {})
            self._write_wiki_page(root, out, stats)
        stats["requires_review"] = bool(wikis)
        stats["review_reason"] = (
            "ADO wiki pages were exported locally; publish/review the GitHub wiki artifact"
            if wikis else ""
        )
        return stats

    def _write_wiki_page(self, page: dict, parent: Path, stats: dict):
        title = (page.get("path", "/Home").split("/")[-1]) or "Home"
        safe = re.sub(r"[^a-zA-Z0-9\-_. ]", "_", title)
        (parent / f"{safe}.md").write_text(
            page.get("content", f"# {title}\n"), encoding="utf-8"
        )
        stats["pages"] += 1
        sub_dir = parent / safe
        for sub in page.get("subPages", []):
            sub_dir.mkdir(exist_ok=True)
            self._write_wiki_page(sub, sub_dir, stats)

    # ── SECRETS ─────────────────────────────────────────────────────────────

    def _migrate_secrets(
        self,
        repo: RepoConfig,
        *,
        _source_payload: Any = None,
        _source_evidence: Optional[dict[str, Any]] = None,
        **_kw: Any,
    ) -> dict:
        if _source_payload is None:
            _source_payload, _source_evidence = self._fetch_verified_scope_payload(
                repo, MigrationScope.SECRETS.value
            )
        var_groups = _source_payload["variable_groups"]
        svc_conns = _source_payload["service_connections"]
        stats = {"variable_groups": len(var_groups),
                 "service_connections": len(svc_conns),
                 "source_snapshot": dict(_source_evidence or {})}
        if self.dry_run:
            stats["dry_run"] = True
            return stats

        # Generate secrets mapping manifest (values cannot be migrated)
        out = output_base() / "secrets" / repo.gh_org / repo.gh_repo
        out.mkdir(parents=True, exist_ok=True)
        import json
        mapping = {
            "instructions": (
                f"Secret VALUES cannot be read from ADO API. "
                f"Use: gh secret set SECRET_NAME --body VALUE "
                f"--repo {repo.gh_org}/{repo.gh_repo}"
            ),
            "variable_groups": [
                {"name": vg.get("name"), "type": vg.get("type"),
                 "variables": [
                     {"name": k, "is_secret": v.get("isSecret", False)}
                     for k, v in vg.get("variables", {}).items()
                 ]}
                for vg in var_groups
            ],
            "service_connections": [
                {"name": sc.get("name"), "type": sc.get("type"),
                 "suggestion": self._suggest_gh_secret(sc)}
                for sc in svc_conns
            ],
        }
        (out / "secrets_mapping.json").write_text(
            json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        stats["manifest_path"] = str(out / "secrets_mapping.json")
        stats["requires_review"] = bool(var_groups or svc_conns)
        stats["review_reason"] = (
            "Secret values and service-connection credentials require operator/OIDC setup"
            if stats["requires_review"] else ""
        )
        return stats

    def _suggest_gh_secret(self, sc: dict) -> str:
        t = sc.get("type", "").lower()
        if "azure" in t:
            return "AZURE_CREDENTIALS or use OIDC (azure/login@v2 with federated credentials)"
        if "docker" in t:
            return "DOCKERHUB_USERNAME + DOCKERHUB_TOKEN"
        if "github" in t:
            return "GH_TOKEN (already available)"
        if "aws" in t:
            return "AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (or OIDC with aws-actions/configure-aws-credentials)"
        if "kubernetes" in t:
            return "KUBE_CONFIG (base64-encoded kubeconfig)"
        if "npm" in t:
            return "NPM_TOKEN"
        if "nuget" in t:
            return "NUGET_API_KEY"
        return f"Review manually — type: {sc.get('type', 'unknown')}"

    # ── BRANCH POLICIES ─────────────────────────────────────────────────────

    def _migrate_branch_policies(
        self,
        repo: RepoConfig,
        *,
        _source_payload: Any = None,
        _source_evidence: Optional[dict[str, Any]] = None,
        **_kw: Any,
    ) -> dict:
        if _source_payload is None:
            _source_payload, _source_evidence = self._fetch_verified_scope_payload(
                repo, MigrationScope.BRANCH_POLICIES.value
            )
        policies = _source_payload
        affected_branches: set[str] = set()
        branch_requirements: dict[str, int] = {}
        evidence: list[dict[str, Any]] = []
        enabled_policy_count = 0
        unsupported = 0
        lossy = 0

        for index, policy in enumerate(policies):
            if not isinstance(policy, Mapping):
                raise RuntimeError(f"Branch policy {index} is not an object")
            policy_type = policy.get("type", {})
            if not isinstance(policy_type, Mapping):
                policy_type = {}
            type_id = str(policy_type.get("id", "")).casefold()
            type_name = str(policy_type.get("displayName", ""))
            settings = policy.get("settings", {})
            if not isinstance(settings, Mapping):
                raise RuntimeError(f"Branch policy {index} settings are not an object")
            raw_scopes = settings.get("scope", [])
            if not isinstance(raw_scopes, list):
                raise RuntimeError(f"Branch policy {index} scope is not a list")
            branches: list[str] = []
            non_exact_scope = False
            for scope_entry in raw_scopes:
                if not isinstance(scope_entry, Mapping):
                    non_exact_scope = True
                    continue
                ref_name = str(scope_entry.get("refName", ""))
                if not ref_name.startswith("refs/heads/") \
                        or not ref_name[len("refs/heads/"):]:
                    non_exact_scope = True
                    continue
                branch = ref_name[len("refs/heads/"):]
                branches.append(branch)
                affected_branches.add(branch)
                match_kind = str(scope_entry.get("matchKind", "Exact"))
                if match_kind.casefold() != "exact":
                    non_exact_scope = True

            enabled = bool(policy.get("isEnabled", True))
            blocking = bool(policy.get("isBlocking", True))
            item_evidence: dict[str, Any] = {
                "policy_id": str(policy.get("id", "")),
                "policy_type_id": type_id,
                "policy_type": type_name,
                "enabled": enabled,
                "blocking": blocking,
                "affected_branches": sorted(set(branches)),
            }
            if not enabled:
                item_evidence["outcome"] = "ignored_disabled"
                evidence.append(item_evidence)
                continue

            enabled_policy_count += 1
            losses: list[str] = []
            if type_id != self.ADO_MINIMUM_REVIEWERS_POLICY:
                item_evidence["outcome"] = "unsupported"
                item_evidence["losses"] = ["policy_type_has_no_safe_mapping"]
                unsupported += 1
                evidence.append(item_evidence)
                continue
            if not blocking:
                losses.append("advisory_policy_cannot_be_represented")
            if non_exact_scope or not branches:
                losses.append("non_exact_or_missing_branch_scope")
            reviewers = settings.get("minimumApproverCount")
            if isinstance(reviewers, bool) or not isinstance(reviewers, int) \
                    or not 1 <= reviewers <= 6:
                losses.append("minimum_approver_count_out_of_range")
            if settings.get("creatorVoteCounts") is True:
                losses.append("creator_vote_semantics_not_represented")
            if settings.get("resetOnSourcePush") is not True:
                losses.append("stale_review_reset_semantics_differ")
            if settings.get("requireVoteOnLastIteration") is True:
                losses.append("last_iteration_vote_not_represented")
            if settings.get("resetRejectionsOnSourcePush") is True:
                losses.append("rejection_reset_semantics_not_represented")

            safe_to_apply = (
                blocking
                and not non_exact_scope
                and bool(branches)
                and isinstance(reviewers, int)
                and not isinstance(reviewers, bool)
                and 1 <= reviewers <= 6
            )
            if safe_to_apply:
                for branch in branches:
                    branch_requirements[branch] = max(
                        branch_requirements.get(branch, 0), reviewers
                    )
                item_evidence["outcome"] = "converted_with_review"
                # Even the supported subset needs target readback validation;
                # the GitHub call cannot prove full ADO semantic parity.
                losses.append("target_policy_parity_requires_readback")
            else:
                item_evidence["outcome"] = "unsupported"
                unsupported += 1
            item_evidence["losses"] = sorted(set(losses))
            if losses:
                lossy += 1
            evidence.append(item_evidence)

        stats: dict[str, Any] = {
            "policies_found": len(policies),
            "enabled_policies": enabled_policy_count,
            "rules_created": 0,
            "existing_rules_preserved": 0,
            "rules_appeared_concurrently": 0,
            "failed": 0,
            "unsupported_policies": unsupported,
            "lossy_policies": lossy,
            "affected_branches": sorted(affected_branches),
            "policy_evidence": evidence,
            "source_snapshot": dict(_source_evidence or {}),
        }
        if enabled_policy_count:
            stats["requires_review"] = True
            stats["review_reason"] = (
                "ADO branch-policy conversion is partial or requires target "
                "readback validation; review policy evidence for every affected branch"
            )
        if self.dry_run:
            stats["dry_run"] = True
            stats["rules_planned"] = len(branch_requirements)
            return stats

        for branch, reviewers in sorted(branch_requirements.items()):
            try:
                outcome = self._ensure_github_branch_protection(
                    repo, branch, reviewers
                )
                if outcome == "created":
                    stats["rules_created"] += 1
                elif outcome == "preserved":
                    stats["existing_rules_preserved"] += 1
                else:
                    stats["rules_appeared_concurrently"] += 1
            except Exception as exc:
                log.warning("branch protection for %s failed: %s", branch, exc)
                stats["failed"] += 1

        return stats
