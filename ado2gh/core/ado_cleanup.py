"""Fenced, evidence-backed post-migration Azure DevOps cleanup."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from ado2gh.clients.ado_client import ADOClient
from ado2gh.governance import (
    ADO_CLEANUP_ACTION,
    SignedApprovalVerifier,
    cleanup_resource,
    governance_resource_digest,
    normalize_governance_settings,
)
from ado2gh.logging_config import console, log
from ado2gh.models import RepoConfig
from ado2gh.pev.contracts import (
    MigrationPlan,
    canonical_ref_snapshot,
    compute_source_refs_digest,
    content_digest,
)
from ado2gh.pev.source_integrity import fetch_project_access_snapshots
from ado2gh.pipelines.approvals import (
    github_environment_configuration_digest,
)
from ado2gh.reporting.post_migration_validator import PASS, PostMigrationValidator
from ado2gh.state.db import StateDB


DESTRUCTIVE_REQUEST_SCHEMA = "ado2gh.destructive-operation/ado-cleanup-v1"
DESTRUCTIVE_OPERATION_KIND = "ado_cleanup"


class _AmbiguousPipelineDisable(RuntimeError):
    """A disable request may have mutated ADO but cannot be reconciled."""


class _ProvenPipelineDisableFailure(RuntimeError):
    """No unaccounted pipeline mutation remains after failure/compensation."""


def _validated_target_web_url(plan: MigrationPlan, target_web_url: str) -> str:
    raw = str(target_web_url or "").rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError(
            "target_web_url must be absolute HTTPS without credentials, "
            "query, or fragment"
        )
    api = urlsplit(str(plan.policy["runtime_context"]["target_api_url"]))
    if api.hostname == "api.github.com":
        valid = parsed.hostname == "github.com" and parsed.port is None \
            and parsed.path in {"", "/"}
    else:
        valid = (
            parsed.hostname == api.hostname
            and parsed.port == api.port
            and parsed.path.rstrip("/") in {"", api.path.removesuffix("/api/v3")}
        )
    if not valid:
        raise ValueError(
            "target_web_url authority is inconsistent with the plan-bound "
            "GitHub API authority"
        )
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))


def _redirect_notice(
    repo: RepoConfig, migration_date: str, target_web_url: str
) -> str:
    return (
        "# Repository Migrated\n\n"
        "This repository has been migrated to GitHub.\n\n"
        f"**New location:** {target_web_url}/{repo.gh_org}/{repo.gh_repo}\n\n"
        "Please update your remotes:\n"
        "```\n"
        f"git remote set-url origin {target_web_url}/{repo.gh_org}/"
        f"{repo.gh_repo}.git\n"
        "```\n\n"
        "This ADO repository is now read-only.\n\n"
        f"_Migrated on {migration_date}_\n"
    )


def build_cleanup_destructive_request(
    plan: MigrationPlan,
    run_id: str,
    repos: list[RepoConfig],
    *,
    disable_pipelines: bool,
    add_redirect: bool,
    archive_repo: bool,
    migration_date: str = "",
    target_web_url: str = "",
) -> dict[str, Any]:
    """Build the exact content addressed request displayed and authorized."""
    plan.validate()
    target_web_url = _validated_target_web_url(plan, target_web_url)
    if add_redirect and not archive_repo:
        raise ValueError("A live redirect requires source archival in the same cutover")
    migration_date = str(migration_date or "")
    if add_redirect and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", migration_date):
        raise ValueError("redirect migration_date must be YYYY-MM-DD")
    planned_by_source = {item.source_key: item for item in plan.repositories}
    inventory_by_source = {
        task.source_key: task
        for task in plan.tasks
        if task.kind == "inventory" and task.scope == "pipelines"
    }
    preflight_by_source = {
        task.source_key: task
        for task in plan.tasks
        if task.kind == "preflight"
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for repo in repos:
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        if source_key in seen or source_key not in planned_by_source:
            raise ValueError(f"cleanup repository is duplicated or unplanned: {source_key}")
        seen.add(source_key)
        planned = planned_by_source[source_key]
        if repo.gh_org != planned.gh_org or repo.gh_repo != planned.gh_repo:
            raise ValueError(f"cleanup target does not match plan for {source_key}")
        pipeline_task = inventory_by_source.get(source_key)
        pipelines = list(pipeline_task.metadata.get("pipelines", ())) \
            if pipeline_task is not None else []
        pattern = re.compile(planned.pipeline_filter) \
            if planned.pipeline_filter else None
        selected = [
            item for item in pipelines
            if pattern is None or pattern.search(str(item.get("pipeline_name", "")))
        ]
        preflight = preflight_by_source[source_key]
        notice = _redirect_notice(
            repo, migration_date, target_web_url
        ) if add_redirect else ""
        rows.append({
            "source_key": source_key,
            "target_key": planned.target_key,
            "source_repo_id": planned.source_repo_id,
            "source_refs_digest": planned.source_refs_digest,
            "target_exists_at_plan": bool(preflight.metadata.get("target_exists")),
            "target_repo_id_at_plan": str(preflight.metadata.get("target_repo_id", "")),
            "target_visibility": str(preflight.metadata.get("target_visibility", "")),
            "pipeline_filter": planned.pipeline_filter,
            "pipeline_inventory_digest": str(
                pipeline_task.metadata.get("inventory_digest", "")
                if pipeline_task is not None else ""
            ),
            "selected_pipeline_receipts_digest": content_digest(selected),
            "selected_pipeline_count": len(selected),
            "actions": {
                "disable_pipelines": bool(disable_pipelines),
                "add_redirect": bool(add_redirect),
                "archive_repo": bool(archive_repo),
            },
            "redirect_migration_date": migration_date if add_redirect else "",
            "redirect_notice_sha256": (
                hashlib.sha256(notice.encode("utf-8")).hexdigest()
                if notice else ""
            ),
        })
    rows.sort(key=lambda item: item["source_key"].casefold())
    if not rows:
        raise ValueError("cleanup request must contain at least one repository")
    return {
        "schema": DESTRUCTIVE_REQUEST_SCHEMA,
        "operation_kind": DESTRUCTIVE_OPERATION_KIND,
        "plan_id": plan.plan_id,
        "run_id": run_id,
        "target_web_url": target_web_url,
        "repositories": rows,
    }


def authorize_cleanup_capability(
    db: StateDB,
    plan: MigrationPlan,
    run_id: str,
    request: Mapping[str, Any],
    *,
    approval_envelope: Optional[Mapping[str, Any]] = None,
    legacy_approval: Optional[Mapping[str, Any]] = None,
    expected_ticket: str = "",
) -> tuple[str, dict[str, Any]]:
    """Authorize one cleanup request through the plan-bound governance API.

    Strict plans require an Ed25519 envelope and atomically consume its nonce
    before the database can mint a destructive capability.  Disabled plans
    retain the legacy operator evidence path for backwards compatibility.
    """
    plan.validate()
    canonical_request = dict(request)
    if canonical_request.get("plan_id") != plan.plan_id \
            or canonical_request.get("run_id") != run_id \
            or canonical_request.get("operation_kind") != ADO_CLEANUP_ACTION:
        raise PermissionError("cleanup authorization does not match plan/run/action")
    policy = normalize_governance_settings(plan.policy.get("governance"))
    verifier = SignedApprovalVerifier(policy)
    resource = cleanup_resource(canonical_request)
    verified = verifier.verify(
        approval_envelope,
        action=ADO_CLEANUP_ACTION,
        resource=resource,
        plan_id=plan.plan_id,
        run_id=run_id,
        expected_ticket=expected_ticket,
    )
    if verified is not None:
        signed_evidence = verified.to_evidence()
        db.claim_pev_governance_approval(
            signed_evidence,
            action=ADO_CLEANUP_ACTION,
            resource_digest=governance_resource_digest(resource),
            plan_id=plan.plan_id,
            run_id=run_id,
        )
        approval = {
            "approval_ticket": verified.claims["ticket"],
            "signed_approval": signed_evidence,
        }
    else:
        if not isinstance(legacy_approval, Mapping) or not legacy_approval:
            raise ValueError("cleanup approval evidence is required")
        approval = dict(legacy_approval)
    capability_id = db.authorize_pev_destructive_capability(
        plan.plan_id,
        run_id,
        DESTRUCTIVE_OPERATION_KIND,
        canonical_request,
        approval=approval,
    )
    return capability_id, approval


class ADOCleanup:
    """Execute an exact approved ADO cutover under GitHub target fencing.

    Live mode intentionally has no legacy constructor path: a caller must
    provide the immutable plan/run, GitHub client, and one-shot destructive
    capability.  This keeps safety controls in the service rather than relying
    on the CLI to have performed them.
    """

    def __init__(
        self,
        ado: ADOClient,
        db: StateDB,
        dry_run: bool = False,
        *,
        gh: Any = None,
        approved_plan: Optional[MigrationPlan] = None,
        approved_run_id: str = "",
        destructive_capability_id: str = "",
        destructive_request: Optional[Mapping[str, Any]] = None,
    ):
        self.ado = ado
        self.gh = gh
        self.db = db
        self.dry_run = bool(dry_run)
        self.plan = approved_plan
        self.run_id = str(approved_run_id or "")
        self.capability_id = str(destructive_capability_id or "")
        self.destructive_request = dict(destructive_request or {})
        self._lease_owner = ""
        self._lease_tokens: dict[str, int] = {}
        self._claim_token = ""
        self._lease_lost = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._target_baselines: dict[str, dict[str, Any]] = {}
        self._live_access_snapshots: dict[str, dict[str, Any]] = {}
        self._planned_by_source = {
            repo.source_key: repo for repo in (
                approved_plan.repositories if approved_plan is not None else ()
            )
        }
        self._inventory_by_source = {
            task.source_key: task
            for task in (approved_plan.tasks if approved_plan is not None else ())
            if task.kind == "inventory" and task.scope == "pipelines"
        }
        if not self.dry_run and (
            self.gh is None
            or self.plan is None
            or not self.run_id
            or not self.capability_id
            or not self.destructive_request
        ):
            raise ValueError(
                "Live cleanup requires GitHub, an exact plan/run, the canonical "
                "destructive request, and its one-shot capability"
            )

    def cleanup_repos(
        self,
        repos: list[RepoConfig],
        disable_pipelines: bool = True,
        add_redirect: bool = True,
        archive_repo: bool = False,
        max_workers: int = 4,
    ) -> list[dict]:
        if add_redirect and not archive_repo and not self.dry_run:
            raise PermissionError(
                "A live redirect is permitted only when the same cutover archives "
                "the source repository"
            )
        console.print(
            f"[bold]ADO Cleanup: {len(repos)} repos[/bold] "
            f"[pipelines={'Y' if disable_pipelines else 'N'} "
            f"redirect={'Y' if add_redirect else 'N'} "
            f"archive={'Y' if archive_repo else 'N'}]"
            f"{'  [DRY RUN]' if self.dry_run else ''}"
        )
        if self.dry_run:
            return [
                self._cleanup_one(
                    repo, disable_pipelines, add_redirect, archive_repo
                )
                for repo in repos
            ]

        migration_date = str(
            self.destructive_request.get("repositories", [{}])[0].get(
                "redirect_migration_date", ""
            )
        ) if add_redirect else ""
        expected_request = build_cleanup_destructive_request(
            self.plan,
            self.run_id,
            repos,
            disable_pipelines=disable_pipelines,
            add_redirect=add_redirect,
            archive_repo=archive_repo,
            migration_date=migration_date,
            target_web_url=str(self.destructive_request.get("target_web_url", "")),
        )
        if expected_request != self.destructive_request:
            raise PermissionError(
                "Cleanup arguments do not match the exact authorized request"
            )
        self._authenticate_governance_capability()
        self._authenticate_plan_run(
            repos,
            disable_pipelines=disable_pipelines,
            add_redirect=add_redirect,
            archive_repo=archive_repo,
        )
        self._live_access_snapshots = (
            fetch_project_access_snapshots(self.ado, self.plan.repositories)
            if callable(getattr(self.ado, "list_project_git_acls", None))
            else {}
        )
        self._start_fencing(repos)
        results: list[dict] = []
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        self._cleanup_one,
                        repo,
                        disable_pipelines,
                        add_redirect,
                        archive_repo,
                    ): repo
                    for repo in repos
                }
                for future in as_completed(futures):
                    repo = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        log.error(
                            "cleanup failed for %s/%s: %s",
                            repo.ado_project,
                            repo.ado_repo,
                            exc,
                        )
                        results.append({
                            "ado_project": repo.ado_project,
                            "ado_repo": repo.ado_repo,
                            "status": "error",
                            "error": str(exc),
                        })
            all_ok = all(item.get("status") == "completed" for item in results)
            try:
                self.db.finish_pev_destructive_capability(
                    self.capability_id,
                    plan_id=self.plan.plan_id,
                    run_id=self.run_id,
                    operation_kind=DESTRUCTIVE_OPERATION_KIND,
                    claimant=self._lease_owner,
                    claim_token=self._claim_token,
                    status="completed" if all_ok else "failed",
                    error="" if all_ok else "one or more cleanup actions failed",
                )
            except RuntimeError:
                # An in-progress receipt is a deliberate crash-stop barrier.
                if all_ok:
                    raise
                log.error(
                    "Cleanup capability remains claimed because a remote action "
                    "has an unresolved receipt"
                )
        finally:
            self._stop_fencing()

        ok = sum(1 for item in results if item.get("status") == "completed")
        console.print(
            f"[bold]Cleanup complete:[/bold] {ok}/{len(results)} repos processed"
        )
        results.sort(key=lambda item: (
            item.get("ado_project", ""), item.get("ado_repo", "")
        ))
        return results

    def _authenticate_governance_capability(self) -> None:
        """Re-verify strict approval at the mutation service boundary."""
        policy = normalize_governance_settings(
            self.plan.policy.get("governance")
        )
        verifier = SignedApprovalVerifier(policy)
        if not verifier.strict:
            return
        row = self.db.get_pev_destructive_capability(self.capability_id)
        if not row or (
            row.get("plan_id") != self.plan.plan_id
            or row.get("run_id") != self.run_id
            or row.get("operation_kind") != DESTRUCTIVE_OPERATION_KIND
        ):
            raise PermissionError(
                "cleanup capability is not bound to the strict governance plan/run"
            )
        try:
            approval = json.loads(str(row.get("approval_json", "")))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("cleanup capability approval evidence is malformed") from exc
        signed_evidence = (
            approval.get("signed_approval")
            if isinstance(approval, dict) else None
        )
        if not isinstance(signed_evidence, dict):
            raise PermissionError(
                "strict cleanup capability lacks signed approval evidence"
            )
        envelope = signed_evidence.get("envelope")
        verified = verifier.verify(
            envelope,
            action=ADO_CLEANUP_ACTION,
            resource=cleanup_resource(self.destructive_request),
            plan_id=self.plan.plan_id,
            run_id=self.run_id,
            expected_ticket=str(approval.get("approval_ticket", "")),
        )
        assert verified is not None
        observed = verified.to_evidence()
        for field in (
            "schema", "key_id", "key_fingerprint", "claims", "envelope"
        ):
            if signed_evidence.get(field) != observed.get(field):
                raise PermissionError(
                    "cleanup capability signed approval evidence was altered"
                )
        self.db.assert_pev_governance_approval(
            signed_evidence,
            action=ADO_CLEANUP_ACTION,
            resource_digest=governance_resource_digest(
                cleanup_resource(self.destructive_request)
            ),
            plan_id=self.plan.plan_id,
            run_id=self.run_id,
        )

    def _authenticate_plan_run(
        self,
        repos: list[RepoConfig],
        *,
        disable_pipelines: bool,
        add_redirect: bool,
        archive_repo: bool,
    ) -> None:
        self.plan.validate()
        cleanup_policy = self.plan.policy.get("cleanup", {})
        if not isinstance(cleanup_policy, Mapping):
            raise PermissionError("Approved cleanup policy is malformed")
        for requested, policy_key, label in (
            (disable_pipelines, "allow_disable_pipelines", "pipeline disabling"),
            (add_redirect, "allow_redirect", "source redirect"),
            (archive_repo, "allow_archive", "source archival"),
        ):
            if requested and cleanup_policy.get(policy_key) is not True:
                raise PermissionError(f"{label} is not approved by the plan")
        selected_keys = {
            f"{repo.ado_project}/{repo.ado_repo}" for repo in repos
        }
        if len(selected_keys) != len(repos) or not selected_keys.issubset(
            self._planned_by_source
        ):
            raise PermissionError("Cleanup repository selection is not an exact plan subset")
        for source_key in selected_keys:
            planned_repo = self._planned_by_source[source_key]
            if disable_pipelines and "pipelines" not in planned_repo.scopes:
                raise PermissionError(
                    f"Pipeline cleanup is outside approved scopes for {source_key}"
                )
            if archive_repo and not planned_repo.archive_source:
                raise PermissionError(
                    f"Source archival is not approved for {source_key}"
                )
        repo_by_source = {repo.source_key: repo for repo in self.plan.repositories}
        self.db.register_pev_plan_capabilities(
            self.plan.plan_id,
            [
                {
                    "source_key": task.source_key,
                    "gh_org": repo_by_source[task.source_key].gh_org,
                    "gh_repo": repo_by_source[task.source_key].gh_repo,
                    "scope": task.scope,
                    "input_digest": content_digest(task.to_dict()),
                }
                for task in self.plan.tasks
                if task.kind == "execute"
            ],
        )
        # Imported lazily to avoid the executor -> core package -> cleanup
        # import cycle during command/module initialization.
        from ado2gh.pev.validator import PEVValidator

        _run, task_rows, planned = PEVValidator(
            self.ado, self.gh, self.db
        )._load_execution_state(self.plan, self.run_id)
        non_completed = []
        for row in task_rows:
            task = planned[row["task_id"]]
            if task.kind in {"preflight", "inventory", "execute", "validate"} \
                    and row.get("status") != "completed":
                non_completed.append(f"{task.source_key}:{task.kind}/{task.scope}")
        if non_completed:
            raise PermissionError(
                "Cleanup requires exact completed plan tasks: "
                + ", ".join(sorted(non_completed))
            )
        if disable_pipelines:
            self._require_exact_pipeline_conversion_receipts(repos)

    def _require_exact_pipeline_conversion_receipts(
        self, repos: list[RepoConfig]
    ) -> None:
        from ado2gh.pev.executor import plan_wave_id

        rows = self.db.get_wave_pipeline_migrations(
            plan_wave_id(self.plan),
            pev_plan_id=self.plan.plan_id,
            pev_run_id=self.run_id,
        )
        by_identity = {
            (
                str(row.get("project", "")),
                str(row.get("repo_name", "")),
                int(row.get("pipeline_id", -1)),
                str(row.get("pipeline_type", "")),
            ): row
            for row in rows
        }
        secret_metadata_cache: dict[tuple[str, str], dict[str, str]] = {}

        def parse_stats(row: Mapping[str, Any]) -> dict[str, Any]:
            raw = row.get("transform_stats") or "{}"
            if isinstance(raw, Mapping):
                return dict(raw)
            try:
                parsed = json.loads(str(raw))
            except (TypeError, ValueError) as exc:
                raise PermissionError(
                    "Pipeline conversion receipt statistics are malformed"
                ) from exc
            if not isinstance(parsed, dict):
                raise PermissionError(
                    "Pipeline conversion receipt statistics are malformed"
                )
            return parsed

        def parse_utc(value: Any, field: str) -> datetime:
            text = str(value or "")
            if not text.endswith("Z"):
                raise PermissionError(f"Pipeline credential {field} is invalid")
            try:
                parsed = datetime.fromisoformat(text[:-1] + "+00:00")
            except ValueError as exc:
                raise PermissionError(
                    f"Pipeline credential {field} is invalid"
                ) from exc
            return parsed.astimezone(timezone.utc)

        def live_secret_versions(repo: RepoConfig) -> dict[str, str]:
            key = (repo.gh_org, repo.gh_repo)
            cached = secret_metadata_cache.get(key)
            if cached is not None:
                return cached
            reader = getattr(self.gh, "list_actions_secret_metadata", None)
            if not callable(reader):
                raise PermissionError(
                    "Cleanup cannot refresh GitHub secret metadata"
                )

            def observe() -> dict[str, str]:
                rows = reader(repo.gh_org, repo.gh_repo)
                if not isinstance(rows, list):
                    raise PermissionError(
                        "GitHub secret metadata inventory is malformed"
                    )
                observed: dict[str, str] = {}
                for item in rows:
                    if not isinstance(item, Mapping):
                        raise PermissionError(
                            "GitHub secret metadata entry is malformed"
                        )
                    name = str(item.get("name") or "")
                    updated_at = str(item.get("updated_at") or "")
                    if not name or not updated_at or name in observed:
                        raise PermissionError(
                            "GitHub secret metadata inventory is ambiguous"
                        )
                    observed[name] = updated_at
                return observed

            first = observe()
            second = observe()
            if first != second:
                raise PermissionError(
                    "GitHub secret metadata changed during cleanup authorization"
                )
            secret_metadata_cache[key] = first
            return first

        def refresh_credential_readiness(
            repo: RepoConfig, row: Mapping[str, Any]
        ) -> None:
            external = parse_stats(row).get(
                "external_configuration_evidence", {}
            )
            if not isinstance(external, Mapping):
                raise PermissionError(
                    "Pipeline external-configuration evidence is malformed"
                )
            count = external.get("credential_attestation_count", 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise PermissionError(
                    "Pipeline credential attestation count is malformed"
                )
            if count == 0:
                return
            verified_at = parse_utc(
                external.get("credential_verified_at"), "verified_at"
            )
            valid_until = parse_utc(
                external.get("credential_valid_until"), "valid_until"
            )
            now = datetime.now(timezone.utc)
            if verified_at > now or valid_until <= now or valid_until <= verified_at:
                raise PermissionError(
                    "Pipeline credential canary expired before ADO cleanup; "
                    "rerun pipeline conversion with a fresh canary"
                )

            target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            target_id = str(
                target.get("node_id") or target.get("id") or ""
            ).strip() if isinstance(target, Mapping) else ""
            if not target_id or target_id != str(
                external.get("target_repository_id") or ""
            ):
                raise PermissionError(
                    "Pipeline credential target repository identity drifted"
                )

            required = external.get("required_secret_names")
            expected_versions = external.get("secret_metadata_versions")
            if (
                not isinstance(required, list)
                or any(not isinstance(name, str) for name in required)
                or len(set(required)) != len(required)
                or not isinstance(expected_versions, Mapping)
                or set(expected_versions) != set(required)
            ):
                raise PermissionError(
                    "Pipeline credential secret-version evidence is incomplete"
                )
            live_versions = live_secret_versions(repo)
            if any(
                live_versions.get(name) != str(expected_versions[name])
                for name in required
            ):
                raise PermissionError(
                    "GitHub credential changed after pipeline validation; "
                    "rerun the signed canary before cleanup"
                )

            checkouts = external.get("repository_checkouts", [])
            if not isinstance(checkouts, list):
                raise PermissionError(
                    "Pipeline checkout readiness evidence is malformed"
                )
            resolver = getattr(self.gh, "get_commit_sha", None)
            for checkout in checkouts:
                if not isinstance(checkout, Mapping):
                    raise PermissionError(
                        "Pipeline checkout readiness evidence is malformed"
                    )
                slug = str(checkout.get("repository") or "")
                if slug.count("/") != 1 or not callable(resolver):
                    raise PermissionError(
                        "Cleanup cannot refresh external checkout readiness"
                    )
                owner, name = slug.split("/", 1)
                live_repo = self.gh.get_repo(owner, name)
                live_id = str(
                    live_repo.get("node_id") or live_repo.get("id") or ""
                ).strip() if isinstance(live_repo, Mapping) else ""
                live_sha = str(resolver(
                    owner, name, str(checkout.get("ref") or "")
                )).lower()
                if (
                    live_id != str(checkout.get("target_repo_id") or "")
                    or live_sha != str(checkout.get("resolved_sha") or "").lower()
                ):
                    raise PermissionError(
                        "External checkout changed after credential validation"
                    )
        for repo in repos:
            source_key = f"{repo.ado_project}/{repo.ado_repo}"
            task = self._inventory_by_source.get(source_key)
            if task is None:
                raise PermissionError(
                    f"Pipeline cleanup is not planned for {source_key}"
                )
            pattern = re.compile(repo.pipeline_filter) if repo.pipeline_filter else None
            for receipt in task.metadata.get("pipelines", ()):
                name = str(receipt.get("pipeline_name", ""))
                if pattern is not None and not pattern.search(name):
                    continue
                identity = (
                    repo.ado_project,
                    repo.ado_repo,
                    int(receipt["pipeline_id"]),
                    str(receipt["pipeline_type"]),
                )
                row = by_identity.get(identity)
                if not row or row.get("status") != "completed" or (
                    row.get("pev_plan_id") != self.plan.plan_id
                    or row.get("pev_run_id") != self.run_id
                    or row.get("gh_org") != repo.gh_org
                    or row.get("gh_repo") != repo.gh_repo
                    or not str(row.get("source_fingerprint", ""))
                ):
                    raise PermissionError(
                        "Pipeline cleanup requires an exact completed conversion "
                        f"receipt for {source_key} pipeline "
                        f"{identity[2]}/{identity[3]}"
                    )
                refresh_credential_readiness(repo, row)

    def _start_fencing(self, repos: list[RepoConfig]) -> None:
        self._lease_owner = f"ado_cleanup_{uuid4().hex}"
        targets = [(repo.gh_org, repo.gh_repo) for repo in repos]
        tokens = self.db.acquire_pev_target_leases(
            self.plan.plan_id,
            self.run_id,
            self._lease_owner,
            targets,
            ttl_seconds=300,
        )
        if tokens is None:
            raise RuntimeError(
                "Cleanup cannot start because a target is fenced or has an "
                "unresolved remote operation"
            )
        self._lease_tokens = tokens
        try:
            self._claim_token = self.db.claim_pev_destructive_capability(
                self.capability_id,
                self.plan.plan_id,
                self.run_id,
                DESTRUCTIVE_OPERATION_KIND,
                self.destructive_request,
                self._lease_owner,
            )
        except Exception:
            self.db.release_pev_target_leases(
                self.plan.plan_id,
                self.run_id,
                self._lease_owner,
                tokens,
            )
            self._lease_tokens = {}
            raise

        def heartbeat() -> None:
            while not self._heartbeat_stop.wait(100):
                try:
                    ok = self.db.renew_pev_target_leases(
                        self.plan.plan_id,
                        self.run_id,
                        self._lease_owner,
                        self._lease_tokens,
                        ttl_seconds=300,
                    )
                except Exception:
                    ok = False
                if not ok:
                    self._lease_lost.set()
                    return

        self._heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"ado2gh-cleanup-lease-{self.run_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_fencing(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)
        if self._lease_tokens:
            self.db.release_pev_target_leases(
                self.plan.plan_id,
                self.run_id,
                self._lease_owner,
                self._lease_tokens,
            )

    def _assert_authority(self, repo: RepoConfig) -> None:
        if self._lease_lost.is_set():
            raise RuntimeError("Cleanup target lease was lost; source write blocked")
        target_key = self.db._mapping_key(repo.gh_org, repo.gh_repo)
        token = self._lease_tokens.get(target_key)
        if token is None:
            raise PermissionError("Cleanup has no target lease for repository")
        self.db.assert_pev_target_lease(
            repo.gh_org,
            repo.gh_repo,
            plan_id=self.plan.plan_id,
            run_id=self.run_id,
            lease_owner=self._lease_owner,
            fencing_token=token,
        )
        self.db.assert_pev_destructive_capability(
            self.capability_id,
            plan_id=self.plan.plan_id,
            run_id=self.run_id,
            operation_kind=DESTRUCTIVE_OPERATION_KIND,
            claimant=self._lease_owner,
            claim_token=self._claim_token,
            request=self.destructive_request,
        )

    def _cleanup_one(
        self,
        repo: RepoConfig,
        disable_pipelines: bool,
        add_redirect: bool,
        archive_repo: bool,
    ) -> dict:
        result: dict[str, Any] = {
            "ado_project": repo.ado_project,
            "ado_repo": repo.ado_repo,
            "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
            "status": "completed",
            "actions": {},
        }
        if self.dry_run:
            if disable_pipelines:
                result["actions"]["disable_pipelines"] = {"dry_run": True}
            if add_redirect:
                result["actions"]["redirect"] = {"dry_run": True}
            if archive_repo:
                result["actions"]["archive"] = {"dry_run": True}
            return result

        source = self._verified_source(repo)
        self._verify_live_parity(repo)
        self._target_baselines[repo.gh_org + "/" + repo.gh_repo] = \
            self._capture_target_baseline(repo)

        redirect_snapshot = None
        if add_redirect:
            source = self._verified_source(repo)
            result["actions"]["redirect"] = self._add_redirect_readme(
                repo, source
            )
            if result["actions"]["redirect"].get("status") != "added":
                result["status"] = "failed"
                return result
            redirect_snapshot = result["actions"]["redirect"]["source_snapshot"]
        if archive_repo:
            expected = redirect_snapshot or self._approved_source_snapshot(repo)
            try:
                result["actions"]["archive"] = self._archive_repo(repo, expected)
            except Exception as exc:
                result["actions"]["archive"] = {
                    "status": "failed", "reason": str(exc)
                }
                if redirect_snapshot is not None:
                    result["actions"]["redirect_rollback"] = \
                        self._revert_redirect(
                            repo, redirect_snapshot, source
                        )
                result["status"] = "failed"
                return result
            if result["actions"]["archive"].get("status") != "archived":
                # Never disable CI while the source remains writable. A failed
                # freeze is reconciled before any pipeline definition changes,
                # including removing the redirect with an exact ref CAS.
                if redirect_snapshot is not None:
                    result["actions"]["redirect_rollback"] = \
                        self._revert_redirect(
                            repo, redirect_snapshot, source
                        )
                result["status"] = "failed"
                return result
        if disable_pipelines:
            try:
                result["actions"]["disable_pipelines"] = self._disable_pipelines(
                    repo, source
                )
            except _AmbiguousPipelineDisable as exc:
                # An in-flight write or failed compensation cannot be proven.
                # Keep the repository frozen and redirect in place so no new
                # source run can race an unknown partial cutover.
                result["actions"]["disable_pipelines"] = {
                    "status": "failed",
                    "ambiguous_remote_state": True,
                    "reason": str(exc),
                }
                result["status"] = "failed"
                return result
            except _ProvenPipelineDisableFailure as exc:
                # Every known write was either never dispatched or exactly
                # compensated. Unwind archive and redirect as one failed
                # cutover instead of leaving harmless drift partially applied.
                result["actions"]["disable_pipelines"] = {
                    "status": "failed",
                    "compensated": True,
                    "ambiguous_remote_state": False,
                    "reason": str(exc),
                }
                if archive_repo:
                    expected = redirect_snapshot or source
                    result["actions"]["archive_rollback"] = \
                        self._restore_archive_after_pipeline_failure(
                            repo, expected
                        )
                if redirect_snapshot is not None:
                    result["actions"]["redirect_rollback"] = \
                        self._revert_redirect(repo, redirect_snapshot, source)
                result["status"] = "failed"
                return result
            disable_result = result["actions"]["disable_pipelines"]
            if disable_result.get("status") != "completed" \
                    and disable_result.get("compensated") is True:
                if archive_repo:
                    expected = redirect_snapshot or source
                    result["actions"]["archive_rollback"] = \
                        self._restore_archive_after_pipeline_failure(
                            repo, expected
                        )
                if redirect_snapshot is not None:
                    result["actions"]["redirect_rollback"] = \
                        self._revert_redirect(repo, redirect_snapshot, source)
                result["status"] = "failed"
                return result

        for action in result["actions"].values():
            if not isinstance(action, dict) or action.get("status") in {
                "failed", "error"
            } or int(action.get("failed", 0) or 0):
                result["status"] = "failed"
        return result

    def _approved_source_snapshot(self, repo: RepoConfig) -> dict[str, Any]:
        planned = self._planned_by_source[
            f"{repo.ado_project}/{repo.ado_repo}"
        ]
        return {
            "source_repo_id": planned.source_repo_id,
            "source_name": planned.ado_repo,
            "default_branch": planned.default_branch,
            "source_branch_refs": tuple(planned.source_branch_refs),
            "source_tag_refs": tuple(planned.source_tag_refs),
            "source_refs_digest": planned.source_refs_digest,
        }

    def _observe_source_snapshot(self, repo: RepoConfig, repo_id: str) -> dict[str, Any]:
        source = self.ado.get_repo(repo.ado_project, repo_id)
        raw_default = source.get("defaultBranch")
        if raw_default in (None, ""):
            default_branch = ""
        elif isinstance(raw_default, str) and raw_default.startswith("refs/heads/") \
                and raw_default[len("refs/heads/"):]:
            default_branch = raw_default[len("refs/heads/"):]
        else:
            raise RuntimeError("ADO returned an invalid default branch")
        branches = canonical_ref_snapshot(
            self.ado.list_refs(repo.ado_project, repo_id, "heads/"), "heads"
        )
        tags = canonical_ref_snapshot(
            self.ado.list_refs(repo.ado_project, repo_id, "tags/"), "tags"
        )
        return {
            "source_repo_id": str(source.get("id", "")).strip(),
            "source_name": str(source.get("name", "")).strip(),
            "default_branch": default_branch,
            "source_branch_refs": branches,
            "source_tag_refs": tags,
            "source_refs_digest": compute_source_refs_digest(branches, tags),
            "is_disabled": bool(source.get("isDisabled", False)),
        }

    def _verified_source(
        self, repo: RepoConfig, expected: Optional[Mapping[str, Any]] = None,
        *, expected_disabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        expected_snapshot = dict(expected or self._approved_source_snapshot(repo))
        observed = self._observe_source_snapshot(
            repo, str(expected_snapshot["source_repo_id"])
        )
        compare_fields = (
            "source_repo_id", "source_name", "default_branch",
            "source_branch_refs", "source_tag_refs", "source_refs_digest",
        )
        mismatches = []
        for field in compare_fields:
            lhs, rhs = observed[field], expected_snapshot[field]
            if field == "source_name":
                matches = str(lhs).casefold() == str(rhs).casefold()
            else:
                matches = lhs == rhs
            if not matches:
                mismatches.append(field)
        if expected_disabled is not None \
                and observed["is_disabled"] is not expected_disabled:
            mismatches.append("is_disabled")
        if mismatches:
            raise RuntimeError(
                f"ADO source drift blocks cleanup for {repo.ado_project}/"
                f"{repo.ado_repo}: {', '.join(mismatches)}"
            )
        return observed

    def _make_post_validator(self) -> PostMigrationValidator:
        from ado2gh.pev.executor import plan_wave_id

        approved_sources = {
            repo.source_key: {
                "source_repo_id": repo.source_repo_id,
                "default_branch": repo.default_branch,
                "source_head_sha": repo.source_head_sha,
                "source_branch_refs": dict(repo.source_branch_refs),
                "source_tag_refs": dict(repo.source_tag_refs),
                "source_refs_digest": repo.source_refs_digest,
            }
            for repo in self.plan.repositories
        }
        pipeline_snapshots = {
            task.source_key: dict(task.metadata)
            for task in self.plan.tasks
            if task.kind == "inventory" and task.scope == "pipelines"
        }
        scope_snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        for task in self.plan.tasks:
            if task.kind == "execute" and "source_snapshot" in task.metadata:
                scope_snapshots.setdefault(task.source_key, {})[task.scope] = \
                    dict(task.metadata["source_snapshot"])
        target_snapshots = {
            task.source_key: dict(task.metadata)
            for task in self.plan.tasks if task.kind == "preflight"
        }
        delivery = self.plan.policy.get("pipeline_delivery", {})
        staging = str(delivery.get("branch", "")) \
            if isinstance(delivery, dict) and delivery.get("mode") == "pull_request" \
            else ""
        validator = PostMigrationValidator(
            self.ado,
            self.gh,
            self.db,
            approved_sources,
            pipeline_snapshots,
            plan_wave_id(self.plan),
            staging,
            self.plan.plan_id,
            self.run_id,
            scope_snapshots,
            target_snapshots,
            bool(self.plan.policy.get("source_selection", {}).get(
                "include_unlinked_work_items", False
            )),
        )
        validator.approved_access_snapshots = {
            item.source_key: dict(item.source_access_snapshot)
            for item in self.plan.repositories
        }
        validator.live_access_snapshots = dict(self._live_access_snapshots)
        return validator

    def _verify_live_parity(self, repo: RepoConfig) -> None:
        self._assert_authority(repo)
        result = self._make_post_validator()._validate_one(repo)
        required = {
            "repo_exists", "default_branch", "head_commit", "branches",
            "tags", "target_snapshot_stable",
        }
        if "pipelines" in (repo.scopes or []):
            required.add("workflows")
        failed = sorted(
            name for name in required
            if result.get("checks", {}).get(name, {}).get("verdict") != PASS
        )
        if result.get("overall") != PASS or failed:
            raise RuntimeError(
                "Exact live GitHub parity blocks ADO cleanup for "
                f"{repo.ado_project}/{repo.ado_repo}: "
                + ", ".join(failed or ["overall validation"])
            )
        self.db.record_validation_evidence(
            self.run_id,
            "cleanup_pre_mutation_parity",
            "pass",
            evidence={
                "capability_id": self.capability_id,
                "source_key": f"{repo.ado_project}/{repo.ado_repo}",
                "target_key": f"{repo.gh_org}/{repo.gh_repo}",
                "checks": result.get("checks", {}),
                "validated_at": result.get("validated_at", ""),
            },
        )

    def _capture_target_baseline(self, repo: RepoConfig) -> dict[str, Any]:
        validator = self._make_post_validator()
        metadata = self.gh.get_repo(repo.gh_org, repo.gh_repo)
        workflows = validator._list_github_workflows(repo.gh_org, repo.gh_repo)
        actions = self.gh.get_actions_permissions(repo.gh_org, repo.gh_repo)
        if not isinstance(metadata, Mapping) or not isinstance(actions, Mapping):
            raise RuntimeError("GitHub target guard response is malformed")
        return {
            "repo_id": str(metadata.get("node_id") or metadata.get("id") or ""),
            "visibility": metadata.get("visibility"),
            "default_branch": metadata.get("default_branch"),
            "branches": validator._list_github_branches(
                repo.gh_org, repo.gh_repo
            ),
            "tags": validator._list_github_tags(repo.gh_org, repo.gh_repo),
            "workflows": sorted(
                (
                    str(item.get("id", "")), str(item.get("path", "")),
                    str(item.get("state", "")),
                )
                for item in workflows
            ),
            "actions_permissions": dict(actions),
            "target_access": self._capture_target_access_guard(repo),
            "pipeline_runtime": self._capture_pipeline_runtime_guard(
                repo, dict(actions)
            ),
        }

    def _capture_target_access_guard(self, repo: RepoConfig) -> dict[str, Any]:
        if not repo.access_policy_approved:
            return {"access_policy_approved": False}
        collaborator_reader = getattr(self.gh, "list_repo_collaborators", None)
        team_reader = getattr(self.gh, "list_repo_teams", None)
        member_reader = getattr(self.gh, "list_team_members", None)
        invitation_reader = getattr(self.gh, "list_repo_invitations", None)
        deploy_key_reader = getattr(self.gh, "list_repo_deploy_keys", None)
        if not all(callable(item) for item in (
            collaborator_reader, team_reader, member_reader,
            invitation_reader, deploy_key_reader,
        )):
            raise RuntimeError(
                "GitHub client cannot guard repository access, invitations, "
                "deploy keys, and team membership"
            )
        org = self.gh.get_org(repo.gh_org)
        if not isinstance(org, Mapping) or org.get(
            "default_repository_permission"
        ) not in {"none", "read", "write", "admin"}:
            raise RuntimeError(
                "GitHub organization base repository permission is unreadable"
            )
        collaborators = collaborator_reader(repo.gh_org, repo.gh_repo)
        teams = team_reader(repo.gh_org, repo.gh_repo)
        invitations = invitation_reader(repo.gh_org, repo.gh_repo)
        deploy_keys = deploy_key_reader(repo.gh_org, repo.gh_repo)

        def identity(item: Mapping[str, Any]) -> str:
            return str(item.get("node_id") or item.get("id") or "")

        collaborator_rows = sorted(
            ({
                "id": identity(item),
                "login": str(item.get("login") or ""),
                "role_name": str(item.get("role_name") or ""),
                "permissions": {
                    str(key): bool(value)
                    for key, value in sorted(
                        dict(item.get("permissions") or {}).items()
                    )
                },
            } for item in collaborators),
            key=lambda item: item["id"],
        )
        team_rows = sorted(
            ({
                "id": identity(item),
                "slug": str(item.get("slug") or ""),
                "permission": str(item.get("permission") or ""),
                "parent_id": identity(item.get("parent") or {})
                if isinstance(item.get("parent"), Mapping) else "",
            } for item in teams),
            key=lambda item: item["id"],
        )
        if any(not item["id"] for item in collaborator_rows + team_rows):
            raise RuntimeError("GitHub access inventory contains no immutable ID")
        team_members: dict[str, list[str]] = {}
        for item in team_rows:
            members = member_reader(repo.gh_org, item["slug"])
            team_members[item["slug"]] = sorted(
                identity(member) for member in members
            )
            if any(not member_id for member_id in team_members[item["slug"]]):
                raise RuntimeError("GitHub team member has no immutable ID")
        invitation_rows = sorted(
            ({
                "id": identity(item),
                "permission": str(item.get("permissions") or ""),
                "invitee_id": identity(item.get("invitee") or {})
                if isinstance(item.get("invitee"), Mapping) else "",
                "inviter_id": identity(item.get("inviter") or {})
                if isinstance(item.get("inviter"), Mapping) else "",
            } for item in invitations),
            key=lambda item: item["id"],
        )
        deploy_key_rows = sorted(
            ({
                "id": identity(item),
                "read_only": bool(item.get("read_only", False)),
            } for item in deploy_keys),
            key=lambda item: item["id"],
        )
        if any(
            not item["id"] for item in invitation_rows + deploy_key_rows
        ):
            raise RuntimeError(
                "GitHub invitation or deploy key has no immutable ID"
            )
        return {
            "access_policy_approved": True,
            "organization_id": str(org.get("node_id") or org.get("id") or ""),
            "base_repository_permission": org["default_repository_permission"],
            "collaborators": collaborator_rows,
            "teams": team_rows,
            "team_members": dict(sorted(team_members.items())),
            "pending_invitations": invitation_rows,
            "deploy_keys": deploy_key_rows,
        }

    def _approved_pipeline_runtime_requirements(
        self, repo: RepoConfig
    ) -> dict[str, Any]:
        """Read the runtime dependency set from this plan/run's receipts only."""
        if "pipelines" not in repo.scopes:
            return {
                "required_secret_names": [],
                "environment_configuration_digests": {},
                "runner_labels": [],
                "repository_checkouts": [],
            }
        task = self._inventory_by_source.get(
            f"{repo.ado_project}/{repo.ado_repo}"
        )
        if task is None:
            raise PermissionError("Approved pipeline inventory is missing")
        pattern = re.compile(repo.pipeline_filter) if repo.pipeline_filter else None
        expected = {
            (int(item["pipeline_id"]), str(item["pipeline_type"]))
            for item in task.metadata.get("pipelines", ())
            if pattern is None or pattern.search(str(item.get("pipeline_name", "")))
        }
        rows = self._make_post_validator()._approved_pipeline_migration_rows(repo)
        selected: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            identity = (int(row["pipeline_id"]), str(row["pipeline_type"]))
            if identity not in expected:
                continue
            if identity in selected or row.get("status") != "completed":
                raise PermissionError(
                    "Pipeline runtime guard requires one completed receipt per "
                    f"approved pipeline: {identity!r}"
                )
            selected[identity] = row
        if set(selected) != expected:
            raise PermissionError(
                "Pipeline runtime guard is missing exact plan/run receipts"
            )

        required_secrets: set[str] = set()
        runner_labels: set[str] = set()
        environments: dict[str, str] = {}
        checkouts: dict[tuple[str, str], dict[str, str]] = {}
        for identity in sorted(selected):
            raw = selected[identity].get("transform_stats") or "{}"
            try:
                stats = raw if isinstance(raw, dict) else json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise PermissionError(
                    f"Pipeline {identity!r} has malformed transform evidence"
                ) from exc
            external = stats.get("external_configuration_evidence") \
                if isinstance(stats, dict) else None
            if not isinstance(external, dict) or external.get("verified") is not True:
                raise PermissionError(
                    f"Pipeline {identity!r} has no verified runtime evidence"
                )
            secrets = external.get("required_secret_names", [])
            labels = external.get("runner_labels", [])
            envs = external.get("environment_configuration_digests", {})
            checkout_rows = external.get("repository_checkouts", [])
            if (
                not isinstance(secrets, list)
                or not isinstance(labels, list)
                or not isinstance(envs, dict)
                or not isinstance(checkout_rows, list)
            ):
                raise PermissionError("Pipeline runtime evidence is malformed")
            required_secrets.update(str(item) for item in secrets)
            runner_labels.update(str(item) for item in labels)
            for name, digest in envs.items():
                name, digest = str(name), str(digest)
                if name in environments and environments[name] != digest:
                    raise PermissionError(
                        "Pipeline receipts disagree on environment protection"
                    )
                environments[name] = digest
            for item in checkout_rows:
                if not isinstance(item, Mapping):
                    raise PermissionError(
                        "Pipeline checkout runtime evidence is malformed"
                    )
                checkout = {
                    "repository": str(item.get("repository", "")),
                    "target_repo_id": str(item.get("target_repo_id", "")),
                    "ref": str(item.get("ref", "")),
                    "resolved_sha": str(item.get("resolved_sha", "")).lower(),
                }
                key = (checkout["repository"], checkout["ref"])
                if key in checkouts and checkouts[key] != checkout:
                    raise PermissionError(
                        "Pipeline receipts disagree on checkout resolution"
                    )
                checkouts[key] = checkout
        return {
            "required_secret_names": sorted(required_secrets),
            "environment_configuration_digests": dict(sorted(environments.items())),
            "runner_labels": sorted(runner_labels),
            "repository_checkouts": [checkouts[key] for key in sorted(checkouts)],
        }

    def _capture_pipeline_runtime_guard(
        self, repo: RepoConfig, actions_permissions: Mapping[str, Any]
    ) -> dict[str, Any]:
        requirements = self._approved_pipeline_runtime_requirements(repo)
        allowed_actions = actions_permissions.get("allowed_actions")
        selected_policy: dict[str, Any] = {}
        if allowed_actions == "selected":
            reader = getattr(self.gh, "get_actions_selected_policy", None)
            if not callable(reader):
                raise RuntimeError(
                    "GitHub client cannot guard selected-actions policy"
                )
            policy = reader(repo.gh_org, repo.gh_repo)
            if not isinstance(policy, Mapping):
                raise RuntimeError("GitHub selected-actions policy is malformed")
            selected_policy = dict(policy)

        required_secrets = requirements["required_secret_names"]
        present_secrets: list[str] = []
        secret_reader = getattr(self.gh, "list_actions_secret_names", None)
        if required_secrets:
            if not callable(secret_reader):
                raise RuntimeError("GitHub client cannot guard Actions secrets")
            live_secrets = secret_reader(repo.gh_org, repo.gh_repo)
            missing = sorted(set(required_secrets) - set(live_secrets))
            if missing:
                raise RuntimeError(
                    "Required Actions secret names drifted before cleanup: "
                    + ", ".join(missing)
                )
            present_secrets = list(required_secrets)

        observed_envs: dict[str, str] = {}
        environment_reader = getattr(self.gh, "get_environment", None)
        for name, expected_digest in requirements[
            "environment_configuration_digests"
        ].items():
            if not callable(environment_reader):
                raise RuntimeError(
                    "GitHub client cannot guard environment protection"
                )
            environment = environment_reader(repo.gh_org, repo.gh_repo, name)
            digest = github_environment_configuration_digest(environment)
            if digest != expected_digest:
                raise RuntimeError(
                    f"GitHub environment {name!r} drifted before cleanup"
                )
            observed_envs[name] = digest

        required_labels = requirements["runner_labels"]
        online_labels: set[str] = set()
        runner_reader = getattr(self.gh, "list_actions_runners", None)
        if required_labels:
            if not callable(runner_reader):
                raise RuntimeError("GitHub client cannot guard Actions runners")
            runners = runner_reader(repo.gh_org, repo.gh_repo)
            online_labels = {
                str(label.get("name"))
                for runner in runners if runner.get("status") == "online"
                for label in runner.get("labels", [])
                if isinstance(label, Mapping) and label.get("name")
            }
            missing = sorted(set(required_labels) - online_labels)
            if missing:
                raise RuntimeError(
                    "Required online runner labels drifted before cleanup: "
                    + ", ".join(missing)
                )

        observed_checkouts: list[dict[str, str]] = []
        resolver = getattr(self.gh, "get_commit_sha", None)
        for approved in requirements["repository_checkouts"]:
            slug = approved["repository"]
            if slug.count("/") != 1:
                raise RuntimeError("Approved checkout repository is malformed")
            owner, name = slug.split("/", 1)
            metadata = self.gh.get_repo(owner, name)
            repo_id = str(
                metadata.get("node_id") or metadata.get("id") or ""
            ) if isinstance(metadata, Mapping) else ""
            if repo_id != approved["target_repo_id"]:
                raise RuntimeError(
                    f"Checkout repository identity drifted for {slug}"
                )
            ref = approved["ref"]
            resolved = ""
            if ref:
                if not callable(resolver):
                    raise RuntimeError("GitHub client cannot guard checkout refs")
                resolved = str(resolver(owner, name, ref)).lower()
                if resolved != approved["resolved_sha"]:
                    raise RuntimeError(
                        f"Checkout ref drifted for {slug}@{ref}"
                    )
            observed_checkouts.append({
                "repository": slug,
                "target_repo_id": repo_id,
                "ref": ref,
                "resolved_sha": resolved,
            })

        # Close read-set races inside the guard itself for mutable inventories.
        if required_secrets and secret_reader(
            repo.gh_org, repo.gh_repo
        ) != live_secrets:
            raise RuntimeError(
                "GitHub Actions secret inventory changed during cleanup guard"
            )
        return {
            "requirements_digest": content_digest(requirements),
            "required_secret_names_present": present_secrets,
            "environment_configuration_digests": dict(sorted(observed_envs.items())),
            "required_runner_labels_online": sorted(
                set(required_labels) & online_labels
            ),
            "repository_checkouts": observed_checkouts,
            "selected_actions_policy": selected_policy,
        }

    def _assert_target_unchanged(self, repo: RepoConfig) -> None:
        self._assert_authority(repo)
        key = f"{repo.gh_org}/{repo.gh_repo}"
        expected = self._target_baselines.get(key)
        if expected is None or self._capture_target_baseline(repo) != expected:
            raise RuntimeError(
                f"GitHub target changed after parity validation for {key}; "
                "ADO source write blocked"
            )

    def _begin_action(
        self, repo: RepoConfig, action_key: str, action_kind: str, before: dict
    ) -> str:
        self._assert_target_unchanged(repo)
        target_key = f"{repo.gh_org}/{repo.gh_repo}"
        receipt_before = {
            **before,
            "plan_id": self.plan.plan_id,
            "run_id": self.run_id,
            "target_guard_digest": content_digest(
                self._target_baselines[target_key]
            ),
        }
        return self.db.begin_pev_destructive_action(
            self.capability_id,
            plan_id=self.plan.plan_id,
            run_id=self.run_id,
            operation_kind=DESTRUCTIVE_OPERATION_KIND,
            claimant=self._lease_owner,
            claim_token=self._claim_token,
            action_key=f"{repo.ado_project}/{repo.ado_repo}:{action_key}",
            source_key=f"{repo.ado_project}/{repo.ado_repo}",
            target_key=f"{repo.gh_org}/{repo.gh_repo}",
            action_kind=action_kind,
            before=receipt_before,
        )

    def _finish_action(
        self, receipt_id: str, *, status: str, after: dict, error: str = ""
    ) -> None:
        if not self.db.finish_pev_destructive_action(
            receipt_id,
            capability_id=self.capability_id,
            claimant=self._lease_owner,
            claim_token=self._claim_token,
            status=status,
            after=after,
            error=error,
        ):
            raise RuntimeError("Destructive action receipt completion was lost")

    @staticmethod
    def _response_payload(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _disable_pipelines(self, repo: RepoConfig, _source: dict) -> dict:
        """Disable the approved set and compensate every known prior write."""
        changed: list[dict[str, Any]] = []
        try:
            stats = self._disable_pipelines_impl(repo, _source, changed)
        except _AmbiguousPipelineDisable:
            # The current request may be ambiguous and therefore keeps the
            # repository frozen, but all earlier exact writes are independently
            # reversible and must not be abandoned as a disabled subset.
            if changed:
                try:
                    self._restore_disabled_pipelines(repo, changed)
                except Exception as restore_exc:
                    raise _AmbiguousPipelineDisable(
                        "Pipeline disable compensation could not be proven"
                    ) from restore_exc
            raise
        except Exception as exc:
            try:
                if changed:
                    self._restore_disabled_pipelines(repo, changed)
            except Exception as restore_exc:
                raise _AmbiguousPipelineDisable(
                    "Pipeline disable compensation could not be proven"
                ) from restore_exc
            raise _ProvenPipelineDisableFailure(
                "Pipeline disable failed before an unaccounted mutation"
            ) from exc
        if stats["failed"]:
            self._restore_disabled_pipelines(repo, changed)
            stats["compensated"] = True
        return stats

    def _disable_pipelines_impl(
        self,
        repo: RepoConfig,
        _source: dict,
        changed: list[dict[str, Any]],
    ) -> dict:
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        task = self._inventory_by_source[source_key]
        pattern = re.compile(repo.pipeline_filter) if repo.pipeline_filter else None
        receipts = [
            dict(item) for item in task.metadata.get("pipelines", ())
            if pattern is None or pattern.search(str(item.get("pipeline_name", "")))
        ]
        stats: dict[str, Any] = {
            "disabled": 0, "failed": 0, "total": len(receipts),
            "pipeline_receipts": [],
        }
        for approved in receipts:
            pipeline_id = int(approved["pipeline_id"])
            pipeline_type = str(approved["pipeline_type"])
            approved_revision = approved.get("source_revision")
            if isinstance(approved_revision, bool) or not isinstance(
                approved_revision, int
            ) or approved_revision < 1:
                raise PermissionError(
                    f"Approved pipeline {pipeline_id}/{pipeline_type} has no "
                    "stable source_revision"
                )
            if pipeline_type == "release":
                definition = self.ado.get_release_definition(
                    repo.ado_project, pipeline_id
                )
                endpoint = self.ado.org_url.replace(
                    "dev.azure.com", "vsrm.dev.azure.com"
                ).replace(
                    ".visualstudio.com", ".vsrm.visualstudio.com"
                )
                url = (
                    f"{endpoint}/{self.ado._p(repo.ado_project)}"
                    f"/_apis/release/definitions/{pipeline_id}?{self.ado.API_RELEASE}"
                )
                disabled_field, disabled_value = "isDisabled", True
            elif pipeline_type in {"yaml", "classic"}:
                definition = self.ado.get_build_definition_full(
                    repo.ado_project, pipeline_id
                )
                url = (
                    f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
                    f"/_apis/build/definitions/{pipeline_id}?{self.ado.API}"
                )
                disabled_field, disabled_value = "queueStatus", "disabled"
            else:
                raise PermissionError(
                    f"Unsupported approved pipeline type {pipeline_type!r}"
                )
            observed = {
                "pipeline_id": definition.get("id"),
                "pipeline_name": definition.get("name"),
                "pipeline_type": pipeline_type,
                "source_revision": definition.get("revision"),
                "disabled_state": definition.get(disabled_field),
            }
            if (
                observed["pipeline_id"] != pipeline_id
                or observed["pipeline_name"] != approved.get("pipeline_name")
                or observed["source_revision"] != approved_revision
            ):
                raise RuntimeError(
                    f"Pipeline drift blocks cleanup for {source_key} "
                    f"{pipeline_id}/{pipeline_type}"
                )
            if definition.get(disabled_field) == disabled_value:
                raise RuntimeError(
                    f"Pipeline {pipeline_id}/{pipeline_type} was already disabled "
                    "after planning; revision-bound cleanup is stale"
                )
            receipt_id = self._begin_action(
                repo,
                f"disable_pipeline:{pipeline_type}:{pipeline_id}",
                "disable_ado_pipeline",
                observed,
            )
            update = dict(definition)
            update[disabled_field] = disabled_value
            try:
                response = self.ado.session.put(url, json=update, timeout=30)
            except Exception as exc:
                raise _AmbiguousPipelineDisable(
                    f"Disable request outcome is unknown for pipeline "
                    f"{pipeline_id}/{pipeline_type}"
                ) from exc
            if not response.ok:
                try:
                    readback = (
                        self.ado.get_release_definition(repo.ado_project, pipeline_id)
                        if pipeline_type == "release" else
                        self.ado.get_build_definition_full(repo.ado_project, pipeline_id)
                    )
                except Exception as exc:
                    raise _AmbiguousPipelineDisable(
                        f"Cannot reconcile failed disable for pipeline "
                        f"{pipeline_id}/{pipeline_type}"
                    ) from exc
                if (
                    readback.get("revision") == approved_revision
                    and readback.get(disabled_field)
                    == definition.get(disabled_field)
                ):
                    self._finish_action(
                        receipt_id,
                        status="failed",
                        after={
                            "http_status": response.status_code,
                            "source_revision": approved_revision,
                            "disabled_state": readback.get(disabled_field),
                        },
                        error=f"ADO returned HTTP {response.status_code}",
                    )
                    stats["failed"] += 1
                    break
                if (
                    readback.get(disabled_field) == disabled_value
                    and isinstance(readback.get("revision"), int)
                    and not isinstance(readback.get("revision"), bool)
                    and readback["revision"] > approved_revision
                ):
                    after = {
                        "pipeline_id": pipeline_id,
                        "pipeline_type": pipeline_type,
                        "source_revision": readback["revision"],
                        "disabled_state": readback.get(disabled_field),
                        "http_status": response.status_code,
                        "reconciled_after_error": True,
                    }
                    changed.append({
                        "pipeline_id": pipeline_id,
                        "pipeline_type": pipeline_type,
                        "url": url,
                        "disabled_field": disabled_field,
                        "disabled_value": disabled_value,
                        "original": definition,
                        "disabled_revision": readback["revision"],
                    })
                    self._finish_action(
                        receipt_id, status="completed", after=after
                    )
                    stats["pipeline_receipts"].append(after)
                    stats["disabled"] += 1
                    continue
                # The request may have committed, but the exact remote state
                # is neither the approved before-state nor the requested
                # after-state. Keep the receipt in progress for reconciliation.
                raise _AmbiguousPipelineDisable(
                    f"Ambiguous disable outcome for pipeline "
                    f"{pipeline_id}/{pipeline_type}"
                )
            payload = self._response_payload(response)
            try:
                if pipeline_type == "release":
                    readback = self.ado.get_release_definition(
                        repo.ado_project, pipeline_id
                    )
                else:
                    readback = self.ado.get_build_definition_full(
                        repo.ado_project, pipeline_id
                    )
            except Exception as exc:
                raise _AmbiguousPipelineDisable(
                    f"Cannot read back disable for pipeline "
                    f"{pipeline_id}/{pipeline_type}"
                ) from exc
            response_revision = payload.get("revision")
            readback_revision = readback.get("revision")
            exact_disabled_readback = (
                readback.get(disabled_field) == disabled_value
                and not isinstance(readback_revision, bool)
                and isinstance(readback_revision, int)
                and readback_revision > approved_revision
            )
            if not exact_disabled_readback:
                # Do not close the receipt: the server acknowledged a mutation
                # but its exact outcome cannot be proven.
                raise _AmbiguousPipelineDisable(
                    f"Cannot prove revision-bound disable for pipeline "
                    f"{pipeline_id}/{pipeline_type}"
                )
            if response_revision != readback_revision:
                # The source is in one fully observable, compensable state,
                # but the 2xx response did not carry the same revision. Treat
                # this as a failed cutover and include the current definition
                # in the exact reverse-order compensation set instead of
                # abandoning an archived source with a disabled subset.
                changed.append({
                    "pipeline_id": pipeline_id,
                    "pipeline_type": pipeline_type,
                    "url": url,
                    "disabled_field": disabled_field,
                    "disabled_value": disabled_value,
                    "original": definition,
                    "disabled_revision": readback_revision,
                })
                self._finish_action(
                    receipt_id,
                    status="failed",
                    after={
                        "pipeline_id": pipeline_id,
                        "pipeline_type": pipeline_type,
                        "source_revision": readback_revision,
                        "disabled_state": readback.get(disabled_field),
                        "response_revision": response_revision,
                        "compensation_required": True,
                    },
                    error="ADO response revision did not match exact readback",
                )
                stats["failed"] += 1
                break
            after = {
                "pipeline_id": pipeline_id,
                "pipeline_type": pipeline_type,
                "source_revision": readback_revision,
                "disabled_state": readback.get(disabled_field),
            }
            # Record the exact reversible state before completing the durable
            # receipt. A receipt-store failure must not strand an untracked
            # disabled pipeline outside the compensation set.
            changed.append({
                "pipeline_id": pipeline_id,
                "pipeline_type": pipeline_type,
                "url": url,
                "disabled_field": disabled_field,
                "disabled_value": disabled_value,
                "original": definition,
                "disabled_revision": readback_revision,
            })
            self._finish_action(receipt_id, status="completed", after=after)
            stats["pipeline_receipts"].append(after)
            stats["disabled"] += 1
        stats["status"] = "completed" if stats["failed"] == 0 else "failed"
        return stats

    def _restore_disabled_pipelines(
        self, repo: RepoConfig, changed: list[dict[str, Any]]
    ) -> None:
        for item in reversed(changed):
            pipeline_id = int(item["pipeline_id"])
            pipeline_type = str(item["pipeline_type"])
            current = (
                self.ado.get_release_definition(repo.ado_project, pipeline_id)
                if pipeline_type == "release" else
                self.ado.get_build_definition_full(repo.ado_project, pipeline_id)
            )
            if (
                current.get("revision") != item["disabled_revision"]
                or current.get(item["disabled_field"]) != item["disabled_value"]
            ):
                raise RuntimeError(
                    f"Pipeline {pipeline_id}/{pipeline_type} changed before "
                    "cutover compensation; source remains frozen"
                )
            receipt_id = self._begin_action(
                repo,
                f"restore_pipeline:{pipeline_type}:{pipeline_id}",
                "restore_ado_pipeline_after_cutover_failure",
                {
                    "pipeline_id": pipeline_id,
                    "pipeline_type": pipeline_type,
                    "source_revision": current["revision"],
                    "disabled_state": current.get(item["disabled_field"]),
                },
            )
            update = dict(item["original"])
            update["revision"] = current["revision"]
            response = self.ado.session.put(
                item["url"], json=update, timeout=30
            )
            readback = (
                self.ado.get_release_definition(repo.ado_project, pipeline_id)
                if pipeline_type == "release" else
                self.ado.get_build_definition_full(repo.ado_project, pipeline_id)
            )
            expected_state = item["original"].get(item["disabled_field"])
            revision = readback.get("revision")
            if (
                not response.ok
                or readback.get(item["disabled_field"]) != expected_state
                or isinstance(revision, bool) or not isinstance(revision, int)
                or revision <= current["revision"]
            ):
                raise RuntimeError(
                    f"Pipeline {pipeline_id}/{pipeline_type} could not be "
                    "restored; source remains frozen"
                )
            self._finish_action(
                receipt_id,
                status="completed",
                after={
                    "pipeline_id": pipeline_id,
                    "pipeline_type": pipeline_type,
                    "source_revision": revision,
                    "disabled_state": expected_state,
                },
            )

    def _restore_archive_after_pipeline_failure(
        self, repo: RepoConfig, expected_source: Mapping[str, Any]
    ) -> dict[str, Any]:
        repo_id = str(expected_source["source_repo_id"])
        frozen = self._verified_source(
            repo, expected_source, expected_disabled=True
        )
        receipt_id = self._begin_action(
            repo,
            "restore_archive_after_pipeline_failure",
            "restore_ado_repository_after_cutover_failure",
            {
                "source_repo_id": repo_id,
                "source_refs_digest": frozen["source_refs_digest"],
                "is_disabled": True,
            },
        )
        url = (
            f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
            f"/_apis/git/repositories/{repo_id}?{self.ado.API}"
        )
        response = self.ado.session.patch(
            url, json={"isDisabled": False}, timeout=30
        )
        if not response.ok:
            raise RuntimeError(
                "Pipeline compensation succeeded but the ADO repository could "
                "not be unfrozen"
            )
        restored = self._verified_source(
            repo, expected_source, expected_disabled=False
        )
        self._finish_action(
            receipt_id,
            status="completed",
            after={
                "source_repo_id": repo_id,
                "source_refs_digest": restored["source_refs_digest"],
                "is_disabled": False,
            },
        )
        return {"status": "restored", "is_disabled": False}

    def _add_redirect_readme(self, repo: RepoConfig, source: dict) -> dict:
        request_row = next(
            item for item in self.destructive_request["repositories"]
            if item["source_key"] == f"{repo.ado_project}/{repo.ado_repo}"
        )
        migration_date = str(request_row["redirect_migration_date"])
        notice_content = _redirect_notice(
            repo,
            migration_date,
            str(self.destructive_request["target_web_url"]),
        )
        if hashlib.sha256(notice_content.encode("utf-8")).hexdigest() != \
                request_row["redirect_notice_sha256"]:
            raise PermissionError("Redirect content does not match authorization")
        default_branch = source["default_branch"]
        branch_ref = f"refs/heads/{default_branch}"
        old_object_id = dict(source["source_branch_refs"]).get(branch_ref, "")
        if not old_object_id:
            raise RuntimeError("Approved default-branch ref is missing")
        before = {
            "source_repo_id": source["source_repo_id"],
            "branch_ref": branch_ref,
            "old_object_id": old_object_id,
            "notice_sha256": request_row["redirect_notice_sha256"],
        }
        receipt_id = self._begin_action(
            repo, "add_redirect", "add_ado_redirect_commit", before
        )
        push_body = {
            "refUpdates": [{"name": branch_ref, "oldObjectId": old_object_id}],
            "commits": [{
                "comment": "Add migration notice — repo migrated to GitHub",
                "changes": [{
                    "changeType": "add",
                    "item": {"path": "/MIGRATION_NOTICE.md"},
                    "newContent": {
                        "content": notice_content,
                        "contentType": "rawtext",
                    },
                }],
            }],
        }
        push_url = (
            f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
            f"/_apis/git/repositories/{source['source_repo_id']}/pushes?"
            f"{self.ado.API}"
        )
        response = self.ado.session.post(push_url, json=push_body, timeout=30)
        if not response.ok:
            try:
                unchanged = self._verified_source(repo, source)
            except Exception as exc:
                # A push may have committed despite the failed response. With
                # no response-bound commit ID, automatic continuation is unsafe.
                raise RuntimeError(
                    "ADO redirect outcome is ambiguous; the source changed but "
                    "the API did not return a successful commit receipt"
                ) from exc
            self._finish_action(
                receipt_id,
                status="failed",
                after={
                    "http_status": response.status_code,
                    "source_refs_digest": unchanged["source_refs_digest"],
                },
                error=f"ADO returned HTTP {response.status_code}",
            )
            return {"status": "failed", "http_code": response.status_code}
        payload = self._response_payload(response)
        commit_ids = {
            str(item.get("commitId") or "")
            for item in payload.get("commits", [])
            if isinstance(item, Mapping) and item.get("commitId")
        }
        ref_ids = {
            str(item.get("newObjectId") or "")
            for item in payload.get("refUpdates", [])
            if isinstance(item, Mapping) and item.get("newObjectId")
        }
        new_ids = commit_ids | ref_ids
        if len(new_ids) != 1:
            raise RuntimeError(
                "ADO accepted redirect push without one exact returned commit ID"
            )
        new_object_id = next(iter(new_ids))
        expected = dict(source)
        expected_branches = dict(source["source_branch_refs"])
        expected_branches[branch_ref] = new_object_id
        expected["source_branch_refs"] = tuple(sorted(expected_branches.items()))
        expected["source_refs_digest"] = compute_source_refs_digest(
            expected["source_branch_refs"], expected["source_tag_refs"]
        )
        observed = self._verified_source(repo, expected)
        after = {
            "new_object_id": new_object_id,
            "source_refs_digest": observed["source_refs_digest"],
            "notice_sha256": request_row["redirect_notice_sha256"],
        }
        self._finish_action(receipt_id, status="completed", after=after)
        return {
            "status": "added",
            "redirect_commit": new_object_id,
            "source_snapshot": expected,
        }

    def _revert_redirect(
        self,
        repo: RepoConfig,
        redirect_source: Mapping[str, Any],
        approved_source: Mapping[str, Any],
    ) -> dict[str, Any]:
        """CAS the redirect ref back when the source freeze did not complete."""
        current = self._verified_source(
            repo, redirect_source, expected_disabled=False
        )
        branch_ref = f"refs/heads/{current['default_branch']}"
        redirect_commit = dict(current["source_branch_refs"]).get(branch_ref, "")
        approved_commit = dict(approved_source["source_branch_refs"]).get(
            branch_ref, ""
        )
        if not redirect_commit or not approved_commit \
                or redirect_commit == approved_commit:
            raise RuntimeError("Redirect rollback refs are incomplete")
        receipt_id = self._begin_action(
            repo,
            "revert_redirect_after_archive_failure",
            "restore_ado_default_branch_ref",
            {
                "source_repo_id": current["source_repo_id"],
                "branch_ref": branch_ref,
                "old_object_id": redirect_commit,
                "new_object_id": approved_commit,
                "source_refs_digest": current["source_refs_digest"],
            },
        )
        url = (
            f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
            f"/_apis/git/repositories/{current['source_repo_id']}/refs?"
            f"{self.ado.API}"
        )
        response = self.ado.session.post(
            url,
            json=[{
                "name": branch_ref,
                "oldObjectId": redirect_commit,
                "newObjectId": approved_commit,
            }],
            timeout=30,
        )
        if not response.ok:
            try:
                restored = self._verified_source(
                    repo, approved_source, expected_disabled=False
                )
            except Exception:
                try:
                    unchanged = self._verified_source(
                        repo, redirect_source, expected_disabled=False
                    )
                except Exception as drift:
                    # The source is neither the exact before nor after state.
                    # Leave the receipt open as an intentional crash barrier.
                    raise RuntimeError(
                        "ADO redirect rollback outcome is ambiguous"
                    ) from drift
                self._finish_action(
                    receipt_id,
                    status="failed",
                    after={
                        "http_status": response.status_code,
                        "source_refs_digest": unchanged["source_refs_digest"],
                    },
                    error=f"ADO returned HTTP {response.status_code}",
                )
                return {
                    "status": "failed", "http_code": response.status_code
                }
            self._finish_action(
                receipt_id,
                status="completed",
                after={
                    "http_status": response.status_code,
                    "source_refs_digest": restored["source_refs_digest"],
                    "reconciled_after_error": True,
                },
            )
            return {"status": "reverted", "reconciled_after_error": True}

        try:
            payload = response.json()
        except Exception:
            payload = None
        rows = payload.get("value") if isinstance(payload, Mapping) else payload
        exact_receipt = False
        if isinstance(rows, list) and len(rows) == 1 \
                and isinstance(rows[0], Mapping):
            row = rows[0]
            exact_receipt = (
                row.get("success") is True
                and str(row.get("name", "")) == branch_ref
                and str(row.get("oldObjectId", "")) == redirect_commit
                and str(row.get("newObjectId", "")) == approved_commit
            )
        if not exact_receipt:
            try:
                restored = self._verified_source(
                    repo, approved_source, expected_disabled=False
                )
            except Exception as exc:
                # A successful HTTP status with neither an exact receipt nor an
                # exact after-state cannot be reconciled automatically.
                raise RuntimeError(
                    "ADO accepted redirect rollback without an exact receipt"
                ) from exc
            reconciled = True
        else:
            restored = self._verified_source(
                repo, approved_source, expected_disabled=False
            )
            reconciled = False
        after = {
            "source_repo_id": restored["source_repo_id"],
            "branch_ref": branch_ref,
            "new_object_id": approved_commit,
            "source_refs_digest": restored["source_refs_digest"],
        }
        if reconciled:
            after["reconciled_by_exact_readback"] = True
        self._finish_action(receipt_id, status="completed", after=after)
        return {
            "status": "reverted",
            "source_refs_digest": restored["source_refs_digest"],
            "reconciled_by_exact_readback": reconciled,
        }

    def _archive_repo(
        self, repo: RepoConfig, expected_source: Mapping[str, Any]
    ) -> dict:
        repo_id = str(expected_source["source_repo_id"])
        current = self._verified_source(repo, expected_source)
        prior_disabled = bool(current["is_disabled"])
        if prior_disabled:
            raise RuntimeError(
                "ADO repository was already disabled after planning; cleanup "
                "cannot prove a fresh freeze boundary"
            )
        receipt_id = self._begin_action(
            repo,
            "archive_source",
            "disable_ado_repository",
            {
                "source_repo_id": repo_id,
                "source_refs_digest": expected_source["source_refs_digest"],
                "is_disabled": prior_disabled,
            },
        )
        url = (
            f"{self.ado.org_url}/{self.ado._p(repo.ado_project)}"
            f"/_apis/git/repositories/{repo_id}?{self.ado.API}"
        )
        response = self.ado.session.patch(
            url, json={"isDisabled": True}, timeout=30
        )
        if not response.ok:
            try:
                frozen = self._verified_source(
                    repo, expected_source, expected_disabled=True
                )
            except Exception as freeze_error:
                observed = self._observe_source_snapshot(repo, repo_id)
                if observed["is_disabled"]:
                    return self._restore_after_archive_drift(
                        repo,
                        repo_id,
                        url,
                        prior_disabled,
                        receipt_id,
                        freeze_error,
                    )
                self._finish_action(
                    receipt_id,
                    status="failed",
                    after={
                        "http_status": response.status_code,
                        "source_refs_digest": observed["source_refs_digest"],
                        "is_disabled": False,
                    },
                    error=f"ADO returned HTTP {response.status_code}",
                )
                return {"status": "failed", "http_code": response.status_code}
            else:
                self._finish_action(
                    receipt_id,
                    status="completed",
                    after={
                        "source_repo_id": repo_id,
                        "source_refs_digest": frozen["source_refs_digest"],
                        "is_disabled": True,
                        "http_status": response.status_code,
                        "reconciled_after_error": True,
                    },
                )
                return {
                    "status": "archived",
                    "source_refs_digest": frozen["source_refs_digest"],
                    "reconciled_after_error": True,
                }
        try:
            frozen = self._verified_source(
                repo, expected_source, expected_disabled=True
            )
        except Exception as drift:
            # The freeze succeeded but a contributor may have won the race just
            # before it. Restore the prior enabled state and prove the restore.
            return self._restore_after_archive_drift(
                repo,
                repo_id,
                url,
                prior_disabled,
                receipt_id,
                drift,
            )
        self._finish_action(
            receipt_id,
            status="completed",
            after={
                "source_repo_id": repo_id,
                "source_refs_digest": frozen["source_refs_digest"],
                "is_disabled": True,
            },
        )
        return {
            "status": "archived",
            "source_refs_digest": frozen["source_refs_digest"],
        }

    def _restore_after_archive_drift(
        self,
        repo: RepoConfig,
        repo_id: str,
        url: str,
        prior_disabled: bool,
        archive_receipt_id: str,
        drift: Exception,
    ) -> dict:
        restore_id = self._begin_action(
            repo,
            "restore_after_archive_drift",
            "restore_ado_repository_state",
            {"source_repo_id": repo_id, "is_disabled": True},
        )
        restore = self.ado.session.patch(
            url, json={"isDisabled": prior_disabled}, timeout=30
        )
        restored = self.ado.get_repo(repo.ado_project, repo_id)
        if not restore.ok or bool(restored.get("isDisabled", False)) \
                is not prior_disabled:
            raise RuntimeError(
                "Source drift occurred at freeze and the previous repository "
                "state could not be restored"
            ) from drift
        self._finish_action(
            restore_id,
            status="completed",
            after={"source_repo_id": repo_id, "is_disabled": prior_disabled},
        )
        self._finish_action(
            archive_receipt_id,
            status="failed",
            after={"restored_is_disabled": prior_disabled},
            error=str(drift),
        )
        return {"status": "failed", "reason": str(drift), "restored": True}
