"""Approved-plan executor with durable task receipts and safe resume."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import threading
from types import MappingProxyType
from typing import Any, Callable, Optional
from uuid import uuid4

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope
from ado2gh.pev.contracts import (
    ExecutionResult,
    MigrationPlan,
    PlanTask,
    canonical_ref_snapshot,
    compute_source_refs_digest,
    content_digest,
)
from ado2gh.pev.planner import (
    _non_secret_config,
    _safe_org_url,
    verify_plan_runtime_context,
)
from ado2gh.pev.source_integrity import (
    fetch_project_access_snapshots,
    fetch_project_work_item_payloads,
)
from ado2gh.pipelines.inventory import PipelineInventoryBuilder
from ado2gh.pipelines.llm import normalize_pipeline_llm_settings


def plan_wave_id(plan: MigrationPlan) -> int:
    """Return a stable positive legacy wave id namespaced by plan content."""
    return int(content_digest(plan.plan_id)[:8], 16) & 0x7FFFFFFF


class PEVExecutor:
    def __init__(
        self,
        global_cfg: dict[str, Any],
        ado: Any,
        gh: Any,
        db: Any,
        engine_factory: Callable[..., MigrationEngine] = MigrationEngine,
        inventory_factory: Callable[..., PipelineInventoryBuilder] = PipelineInventoryBuilder,
    ):
        self.cfg = dict(global_cfg)
        pipeline_conversion = dict(self.cfg.get("pipeline_conversion", {}))
        pipeline_conversion.update(normalize_pipeline_llm_settings(
            pipeline_conversion,
            enforce_environment_match=False,
        ))
        self.cfg["pipeline_conversion"] = pipeline_conversion
        self.ado = ado
        self.gh = gh
        self.db = db
        self.engine_factory = engine_factory
        self.inventory_factory = inventory_factory

    def execute(
        self,
        plan: MigrationPlan,
        approved_plan_id: str = "",
        dry_run: bool = False,
        run_id: Optional[str] = None,
        repair: bool = False,
        _isolated_preview: bool = False,
    ) -> ExecutionResult:
        plan.validate()
        if dry_run and not _isolated_preview:
            from ado2gh.state.db import StateDB
            preview = PEVExecutor(
                self.cfg,
                self.ado,
                self.gh,
                StateDB(":memory:"),
                engine_factory=self.engine_factory,
                inventory_factory=self.inventory_factory,
            )
            return preview.execute(
                plan,
                approved_plan_id=approved_plan_id,
                dry_run=True,
                run_id=None,
                repair=repair,
                _isolated_preview=True,
            )
        if not dry_run and approved_plan_id != plan.plan_id:
            raise PermissionError(
                "Live execution requires --approve-plan with the exact immutable plan_id"
            )
        self._verify_context(plan)
        repo_by_source = {repo.source_key: repo for repo in plan.repositories}
        self.db.register_pev_plan_capabilities(
            plan.plan_id,
            [
                {
                    "source_key": task.source_key,
                    "gh_org": repo_by_source[task.source_key].gh_org,
                    "gh_repo": repo_by_source[task.source_key].gh_repo,
                    "scope": task.scope,
                    "input_digest": content_digest(task.to_dict()),
                }
                for task in plan.tasks
                if task.kind == "execute"
            ],
        )

        resumed = bool(run_id)
        if run_id:
            existing_run = self.db.get_pev_run(run_id)
            if not existing_run or existing_run["plan_id"] != plan.plan_id:
                raise ValueError(f"Run {run_id!r} does not belong to plan {plan.plan_id}")
            self.db.upsert_pev_run(
                run_id, plan.plan_id, status="executing",
                config_digest=plan.config_digest,
            )
        else:
            run_id = self.db.create_pev_run(
                plan.plan_id,
                config_digest=plan.config_digest,
                status="executing",
            )

        lease_seconds = int(self.cfg.get("execution_lease_seconds", 300))
        lease_owner = f"executor_{uuid4().hex}"
        task_lease_owner = f"task_executor_{run_id}_{uuid4().hex}"
        self._task_lease_owner = task_lease_owner
        if not self.db.acquire_pev_plan_lease(
            plan.plan_id,
            run_id,
            lease_owner,
            ttl_seconds=lease_seconds,
        ):
            raise RuntimeError(
                f"Plan {plan.plan_id} is already executing in another process"
            )
        try:
            target_leases = self.db.acquire_pev_target_leases(
                plan.plan_id,
                run_id,
                lease_owner,
                [(repo.gh_org, repo.gh_repo) for repo in plan.repositories],
                ttl_seconds=lease_seconds,
            )
        except Exception:
            self.db.release_pev_plan_lease(plan.plan_id, run_id, lease_owner)
            raise
        if target_leases is None:
            self.db.release_pev_plan_lease(plan.plan_id, run_id, lease_owner)
            raise RuntimeError(
                "One or more approved GitHub targets are already leased by "
                "another plan"
            )
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def _heartbeat() -> None:
            interval = max(10.0, lease_seconds / 3)
            while not stop_heartbeat.wait(interval):
                try:
                    plan_renewed = self.db.renew_pev_plan_lease(
                        plan.plan_id,
                        run_id,
                        lease_owner,
                        ttl_seconds=lease_seconds,
                    )
                    targets_renewed = self.db.renew_pev_target_leases(
                        plan.plan_id,
                        run_id,
                        lease_owner,
                        target_leases,
                        ttl_seconds=lease_seconds,
                    )
                    self.db.renew_pev_task_leases(
                        task_lease_owner,
                        ttl_seconds=lease_seconds,
                    )
                except Exception:
                    log.exception("PEV lease heartbeat failed for run %s", run_id)
                    lease_lost.set()
                    return
                if not plan_renewed or not targets_renewed:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=_heartbeat,
            name=f"ado2gh-lease-{run_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = self._execute_with_lease(
                plan,
                run_id,
                resumed=resumed,
                dry_run=dry_run,
                repair=repair,
                lease_lost=lease_lost,
                target_lease_owner=lease_owner,
                target_fencing_tokens=target_leases,
            )
            if lease_lost.is_set():
                raise RuntimeError(
                    "PEV plan lease was lost during execution; validation is blocked"
                )
            return result
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=5)
            self.db.release_pev_target_leases(
                plan.plan_id,
                run_id,
                lease_owner,
                target_leases,
            )
            self.db.release_pev_plan_lease(plan.plan_id, run_id, lease_owner)

    def _execute_with_lease(
        self,
        plan: MigrationPlan,
        run_id: str,
        *,
        resumed: bool,
        dry_run: bool,
        repair: bool,
        lease_lost: threading.Event,
        target_lease_owner: str,
        target_fencing_tokens: dict[str, int],
    ) -> ExecutionResult:
        self._register_tasks(run_id, plan)
        started_at = datetime.now(timezone.utc).isoformat()
        task_by_key = {
            (task.source_key, task.kind, task.scope): task for task in plan.tasks
        }

        preflight_errors = self._run_preflights(
            run_id, plan, task_by_key, repair=repair
        )
        # Inventory writes only to the durable live DB or the isolated preview
        # DB. It must persist its snapshot so the approved digest can be checked.
        inventory_errors, pipeline_snapshots = self._run_inventory(
            run_id, plan, task_by_key, dry_run=False
        )
        access_errors: dict[str, str] = {}
        repos_with_access_receipts = [
            repo for repo in plan.repositories if repo.source_access_snapshot
        ]
        if repos_with_access_receipts:
            if not callable(getattr(self.ado, "list_project_git_acls", None)):
                access_errors = {
                    repo.source_key: "ADO Git ACL inventory is unavailable"
                    for repo in repos_with_access_receipts
                }
            else:
                try:
                    live_access = fetch_project_access_snapshots(
                        self.ado, repos_with_access_receipts
                    )
                except Exception as exc:
                    access_errors = {
                        repo.source_key: f"Cannot verify ADO Git ACLs: {exc}"
                        for repo in repos_with_access_receipts
                    }
                else:
                    access_errors = {
                        repo.source_key: "ADO Git ACLs changed after plan approval"
                        for repo in repos_with_access_receipts
                        if live_access.get(repo.source_key)
                        != repo.source_access_snapshot
                    }
        blocked_sources = (
            set(preflight_errors) | set(inventory_errors) | set(access_errors)
        )
        if lease_lost.is_set():
            raise RuntimeError("PEV plan lease was lost before target execution")

        execution_cfg = dict(self.cfg)
        execution_cfg["enforce_scope_dependencies"] = True
        execution_cfg["pev_run_id"] = run_id
        execution_cfg["pev_plan_id"] = plan.plan_id
        execution_cfg["pev_target_fencing_required"] = True
        execution_cfg["pev_target_lease_owner"] = target_lease_owner
        execution_cfg["pev_target_fencing_tokens"] = dict(
            target_fencing_tokens
        )
        execution_cfg["pev_scope_input_digests"] = {
            repo.source_key: {
                task.scope: content_digest(task.to_dict())
                for task in plan.tasks
                if task.source_key == repo.source_key
                and task.kind == "execute"
            }
            for repo in plan.repositories
        }
        execution_cfg["pev_source_ref_snapshots"] = {
            repo.source_key: {
                "source_repo_id": repo.source_repo_id,
                "default_branch": repo.default_branch,
                "source_head_sha": repo.source_head_sha,
                "source_branch_refs": dict(repo.source_branch_refs),
                "source_tag_refs": dict(repo.source_tag_refs),
                "source_refs_digest": repo.source_refs_digest,
            }
            for repo in plan.repositories
        }
        execution_cfg["pev_non_git_source_snapshots"] = {
            repo.source_key: {
                task.scope: dict(task.metadata["source_snapshot"])
                for task in plan.tasks
                if task.source_key == repo.source_key
                and task.kind == "execute"
                and "source_snapshot" in task.metadata
            }
            for repo in plan.repositories
        }
        execution_cfg["pev_target_snapshots"] = {
            repo.source_key: dict(next(
                task.metadata
                for task in plan.tasks
                if task.source_key == repo.source_key
                and task.kind == "preflight"
            ))
            for repo in plan.repositories
        }
        work_item_repos = [
            repo for repo in plan.repositories
            if MigrationScope.WORK_ITEMS.value in repo.scopes
            and repo.source_key not in blocked_sources
        ]
        execution_cfg["pev_work_item_payloads"] = (
            fetch_project_work_item_payloads(
                self.ado,
                work_item_repos,
                include_unlinked_work_items=bool(
                    self.cfg.get("include_unlinked_work_items", False)
                ),
            )
            if work_item_repos else {}
        )
        engine = self.engine_factory(
            execution_cfg,
            self.ado,
            self.gh,
            self.db,
            dry_run=dry_run,
            pipeline_snapshots=MappingProxyType(dict(pipeline_snapshots)),
        )
        wave_id = plan_wave_id(plan)
        repo_results: dict[str, dict[str, Any]] = {}
        max_workers = max(1, int(self.cfg.get("parallel", 4)))
        executable = [
            repo for repo in plan.repositories if repo.source_key not in blocked_sources
        ]

        def execute_one(repo: Any) -> dict[str, Any]:
            if lease_lost.is_set():
                raise RuntimeError("PEV lease was lost before task claim")
            # Claim only when a worker is ready to execute.  Pre-claiming every
            # repository lets queued task leases expire on large organizations.
            for task in plan.tasks:
                if task.source_key == repo.source_key and task.kind == "execute":
                    self._claim_task(run_id, task, force_recheck=repair)
            if lease_lost.is_set():
                raise RuntimeError("PEV lease was lost before target execution")
            return engine.migrate_repo(
                wave_id,
                repo.to_repo_config(),
                pipeline_parallel=repo.pipeline_parallel,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(execute_one, repo): repo
                for repo in executable
            }
            for future in as_completed(futures):
                repo = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"status": "failed", "scopes": {}, "errors": [str(exc)]}
                repo_results[repo.source_key] = result
                if not lease_lost.is_set():
                    self._record_scope_results(
                        run_id, repo.source_key, result, task_by_key
                    )

        if lease_lost.is_set():
            raise RuntimeError(
                "PEV target or plan lease was lost during repository execution"
            )

        for repo in plan.repositories:
            if repo.source_key in blocked_sources:
                reason = preflight_errors.get(repo.source_key) or inventory_errors.get(
                    repo.source_key
                ) or access_errors.get(repo.source_key, "dependency failed")
                repo_results[repo.source_key] = {
                    "status": "failed",
                    "scopes": {},
                    "errors": [reason],
                }
                for task in plan.tasks:
                    if task.source_key == repo.source_key and task.kind == "execute":
                        # A newly failed dependency must not downgrade a prior
                        # terminal task without first acquiring its lease.
                        self._claim_task(run_id, task, force_recheck=True)
                        self._update_task(
                            run_id, task, "blocked", error=reason,
                            result={"blocked_by_dependency": True},
                        )

        statuses = [result.get("status", "failed") for result in repo_results.values()]
        if dry_run:
            if not statuses or any(item == "failed" for item in statuses):
                status = "dry_run_failed"
            elif all(item in {"completed", "dry_run"} for item in statuses):
                status = "dry_run_passed"
            else:
                status = "dry_run_needs_review"
        elif statuses and all(item == "completed" for item in statuses):
            status = "executed"
        elif any(item == "failed" for item in statuses):
            status = "failed"
        else:
            status = "needs_review"
        completed_at = datetime.now(timezone.utc).isoformat()
        summary = {
            "repositories": len(plan.repositories),
            "completed": sum(item == "completed" for item in statuses),
            "needs_review": sum(item == "partial" for item in statuses),
            "failed": sum(item == "failed" for item in statuses),
            "wave_id": wave_id,
            "dry_run": dry_run,
        }
        if lease_lost.is_set():
            raise RuntimeError("PEV lease was lost before run state commit")
        self.db.upsert_pev_run(
            run_id,
            plan.plan_id,
            status=status,
            config_digest=plan.config_digest,
            summary=summary,
        )
        return ExecutionResult(
            run_id=run_id,
            plan_id=plan.plan_id,
            status=status,
            repository_results=repo_results,
            started_at=started_at,
            completed_at=completed_at,
            resumed=resumed,
        )

    def _verify_context(self, plan: MigrationPlan) -> None:
        verify_plan_runtime_context(plan, self.cfg, self.ado, self.gh)
        current_source = _safe_org_url(
            str(getattr(self.ado, "org_url", "") or self.cfg.get("ado_org_url", ""))
        )
        if current_source.casefold() != plan.source_org_url.casefold():
            raise ValueError(
                f"Plan source {plan.source_org_url} does not match client {current_source}"
            )
        current_digest = content_digest(_non_secret_config(self.cfg))
        if current_digest != plan.config_digest:
            raise ValueError(
                "Execution configuration changed after planning; generate and approve a new plan"
            )

    @staticmethod
    def _db_task_id(run_id: str, task: PlanTask) -> str:
        return f"task_{content_digest({'run': run_id, 'task': task.task_id})[:32]}"

    def _register_tasks(self, run_id: str, plan: MigrationPlan) -> None:
        existing_ids = {
            row["task_id"] for row in self.db.list_pev_tasks(run_id)
        }
        max_attempts = max(
            3,
            int(self.cfg.get("validation", {}).get("max_repair_attempts", 1)) + 3,
        )
        for task in plan.tasks:
            task_id = self._db_task_id(run_id, task)
            if task_id in existing_ids:
                continue
            self.db.upsert_pev_task(
                run_id=run_id,
                kind=task.kind,
                input_digest=content_digest(task.to_dict()),
                source_ref=task.source_key,
                target_ref=task.target_key,
                status="pending",
                task_id=task_id,
                strategy=task.execution_mode,
                dependencies=list(task.dependencies),
                idempotency_key=f"{plan.plan_id}:{task.task_id}",
                result={"plan_task_id": task.task_id, "scope": task.scope},
                max_attempts=max_attempts,
            )

    def _claim_task(
        self,
        run_id: str,
        task: PlanTask,
        *,
        force_recheck: bool,
    ) -> bool:
        """Claim one DAG node before work; completed side effects stay skipped."""
        task_id = self._db_task_id(run_id, task)
        row = self.db.get_pev_task(task_id)
        if not row:
            raise RuntimeError(f"PEV task {task_id} is missing")
        status = str(row.get("status", ""))
        if status == "completed" and not force_recheck:
            return False
        if status in {
            "completed", "failed", "blocked", "needs_review", "cancelled", "skipped"
        }:
            if not self.db.update_pev_task(
                task_id,
                status="retry",
                result={"recheck": force_recheck},
                expected_status=status,
            ):
                raise RuntimeError(f"Could not reset PEV task {task_id} for retry")
        claimed = self.db.claim_pev_task(
            task_id,
            self._task_lease_owner,
            lease_seconds=int(self.cfg.get("execution_lease_seconds", 300)),
        )
        if claimed is None:
            raise RuntimeError(
                f"PEV task {task_id} is already leased or exhausted its attempts"
            )
        return True

    def _run_preflights(
        self,
        run_id: str,
        plan: MigrationPlan,
        task_by_key: dict[tuple[str, str, str], PlanTask],
        repair: bool = False,
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        mapping_policy = plan.policy.get("mapping", {})
        for repo in plan.repositories:
            task = task_by_key[(repo.source_key, "preflight", "")]
            try:
                self._claim_task(run_id, task, force_recheck=True)
                observed = self._observe_source(repo)
                observed_id = observed["source_repo_id"]
                if observed_id != repo.source_repo_id:
                    raise RuntimeError(
                        f"Source repository identity drift: planned {repo.source_repo_id}, "
                        f"observed {observed_id}"
                    )
                if observed["default_branch"] != repo.default_branch:
                    raise RuntimeError(
                        f"Source default branch drift: planned "
                        f"{repo.default_branch!r}, observed "
                        f"{observed['default_branch']!r}"
                    )
                if observed["source_branch_refs"] != repo.source_branch_refs \
                        or observed["source_tag_refs"] != repo.source_tag_refs:
                    raise RuntimeError(
                        f"Source ref drift for {repo.source_key}: planned "
                        f"{repo.source_refs_digest}, observed "
                        f"{observed['source_refs_digest']}"
                    )
                if observed["source_refs_digest"] != repo.source_refs_digest:
                    raise RuntimeError(
                        f"Source ref digest drift for {repo.source_key}: planned "
                        f"{repo.source_refs_digest}, observed "
                        f"{observed['source_refs_digest']}"
                    )
                target_exists = bool(self.gh.repo_exists(repo.gh_org, repo.gh_repo))
                planned_exists = task.metadata.get("target_exists")
                owned_by_run = False
                target_repo_id = ""
                target_size = 0
                target_visibility = "private"
                target_default_branch = ""
                target_branch_refs: tuple[tuple[str, str], ...] = ()
                target_tag_refs: tuple[tuple[str, str], ...] = ()
                target_refs_digest = compute_source_refs_digest((), ())
                if target_exists:
                    target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
                    if not isinstance(target, dict):
                        raise RuntimeError(
                            f"Target {repo.target_key} metadata is not an object"
                        )
                    target_repo_id = str(
                        target.get("node_id") or target.get("id") or ""
                    ).strip()
                    raw_size = target.get("size")
                    if not target_repo_id:
                        raise RuntimeError(
                            f"Target {repo.target_key} has no immutable repository ID"
                        )
                    if isinstance(raw_size, bool) or not isinstance(raw_size, int) \
                            or raw_size < 0:
                        raise RuntimeError(
                            f"Target {repo.target_key} has invalid size metadata"
                        )
                    target_size = raw_size
                    raw_visibility = target.get("visibility")
                    if raw_visibility not in {"private", "internal", "public"}:
                        raise RuntimeError(
                            f"Target {repo.target_key} has invalid visibility metadata"
                        )
                    target_visibility = raw_visibility
                    raw_default = target.get("default_branch", "")
                    if not isinstance(raw_default, str):
                        raise RuntimeError(
                            f"Target {repo.target_key} has invalid default branch metadata"
                        )
                    target_default_branch = raw_default.strip()
                    if planned_exists is True:
                        target_branch_refs, target_tag_refs = (
                            self._observe_target_refs(repo)
                        )
                        target_refs_digest = compute_source_refs_digest(
                            target_branch_refs, target_tag_refs
                        )
                    # A normal --resume must accept a repository this exact
                    # plan/run created before a crash.  Immutable-ID ownership
                    # still blocks a replacement repository at the same name.
                    if planned_exists is False:
                        owned_by_run = bool(
                            self.db.repository_owned_by_run(
                                repo.gh_org,
                                repo.gh_repo,
                                plan_id=plan.plan_id,
                                run_id=run_id,
                                target_repo_id=target_repo_id,
                            )
                        )
                if planned_exists is True:
                    if not target_exists:
                        raise RuntimeError(
                            f"Approved target {repo.target_key} no longer exists"
                        )
                    if target_repo_id != task.metadata.get("target_repo_id"):
                        raise RuntimeError(
                            f"Target repository identity drift for {repo.target_key}"
                        )
                    if target_size != task.metadata.get("target_size"):
                        raise RuntimeError(
                            f"Target repository size drift for {repo.target_key}: "
                            f"planned {task.metadata.get('target_size')}, "
                            f"observed {target_size}"
                        )
                    if target_visibility != task.metadata.get(
                        "target_visibility"
                    ):
                        raise RuntimeError(
                            f"Target visibility drift for {repo.target_key}"
                        )
                    if target_default_branch != task.metadata.get(
                        "target_default_branch"
                    ):
                        raise RuntimeError(
                            f"Target default branch drift for {repo.target_key}"
                        )
                    if (
                        dict(target_branch_refs)
                        != task.metadata.get("target_branch_refs")
                        or dict(target_tag_refs)
                        != task.metadata.get("target_tag_refs")
                        or target_refs_digest
                        != task.metadata.get("target_refs_digest")
                    ):
                        raise RuntimeError(
                            f"Target ref drift for {repo.target_key}: planned "
                            f"{task.metadata.get('target_refs_digest')}, observed "
                            f"{target_refs_digest}"
                        )
                if planned_exists is False and target_exists and not owned_by_run:
                    raise RuntimeError(
                        f"Target {repo.target_key} appeared after plan approval"
                    )
                if (
                    target_exists
                    and mapping_policy.get("existing_target_policy") != "reuse"
                    and not owned_by_run
                ):
                    raise RuntimeError(
                        f"Target {repo.target_key} exists but approved policy is not reuse"
                    )
                self._update_task(
                    run_id, task, "completed",
                    result={
                        "source_repo_id": observed_id,
                        "default_branch": observed["default_branch"],
                        "source_head_sha": observed["source_head_sha"],
                        "source_branch_count": len(
                            observed["source_branch_refs"]
                        ),
                        "source_tag_count": len(observed["source_tag_refs"]),
                        "source_refs_digest": observed["source_refs_digest"],
                        "target_exists": target_exists,
                        "target_repo_id": target_repo_id,
                        "target_size": target_size,
                        "target_visibility": target_visibility,
                        "target_default_branch": target_default_branch,
                        "target_branch_count": len(target_branch_refs),
                        "target_tag_count": len(target_tag_refs),
                        "target_refs_digest": target_refs_digest,
                    },
                )
            except Exception as exc:
                errors[repo.source_key] = str(exc)
                self._update_task(run_id, task, "failed", error=str(exc))
        return errors

    def _observe_source(self, repo: Any) -> dict[str, Any]:
        source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
        if not isinstance(source, dict):
            raise RuntimeError("ADO source repository response must be an object")
        repo_id = str(source.get("id", "")).strip()
        if not repo_id:
            raise RuntimeError(
                f"ADO source {repo.source_key} has no immutable repository ID"
            )
        raw_default = source.get("defaultBranch")
        if raw_default in (None, ""):
            default_branch = ""
        elif isinstance(raw_default, str) and raw_default.startswith(
            "refs/heads/"
        ) and raw_default[len("refs/heads/"):]:
            default_branch = raw_default[len("refs/heads/"):]
        else:
            raise RuntimeError(
                f"ADO source {repo.source_key} returned an invalid defaultBranch"
            )
        helper = getattr(self.ado, "list_refs", None)
        if not callable(helper):
            raise RuntimeError("ADO client does not support complete ref listing")
        branches = canonical_ref_snapshot(
            helper(repo.ado_project, repo_id, "heads/"), "heads"
        )
        tags = canonical_ref_snapshot(
            helper(repo.ado_project, repo_id, "tags/"), "tags"
        )
        head_sha = (
            dict(branches).get(f"refs/heads/{default_branch}", "")
            if default_branch else ""
        )
        return {
            "source_repo_id": repo_id,
            "default_branch": default_branch,
            "source_head_sha": head_sha,
            "source_branch_refs": branches,
            "source_tag_refs": tags,
            "source_refs_digest": compute_source_refs_digest(branches, tags),
        }

    def _observe_target_refs(
        self, repo: Any
    ) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
        helper = getattr(self.gh, "list_git_refs", None)
        if not callable(helper):
            raise RuntimeError(
                "GitHub client does not support complete paginated ref listing"
            )

        def read(namespace: str) -> tuple[tuple[str, str], ...]:
            rows = helper(repo.gh_org, repo.gh_repo, namespace)
            if not isinstance(rows, list):
                raise RuntimeError(
                    f"GitHub refs/{namespace} response must be a complete list"
                )
            converted: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise RuntimeError(
                        f"GitHub refs/{namespace} item {index} must be an object"
                    )
                obj = row.get("object", {})
                if not isinstance(obj, dict):
                    raise RuntimeError(
                        f"GitHub ref {row.get('ref')!r} has no object metadata"
                    )
                converted.append({
                    "name": row.get("ref"),
                    "objectId": obj.get("sha"),
                })
            return canonical_ref_snapshot(converted, namespace)

        return read("heads"), read("tags")

    def _run_inventory(
        self,
        run_id: str,
        plan: MigrationPlan,
        task_by_key: dict[tuple[str, str, str], PlanTask],
        dry_run: bool,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        errors: dict[str, str] = {}
        snapshots: dict[str, Any] = {}
        by_project: dict[str, list[Any]] = {}
        for repo in plan.repositories:
            if MigrationScope.PIPELINES.value in repo.scopes:
                by_project.setdefault(repo.ado_project, []).append(repo)
        for project, repos in by_project.items():
            project_snapshots: dict[str, Any] = {}
            try:
                for repo in repos:
                    self._claim_task(
                        run_id,
                        task_by_key[(repo.source_key, "inventory", "pipelines")],
                        force_recheck=True,
                    )
                builder = self.inventory_factory(
                    self.ado,
                    self.db,
                    parallel=int(self.cfg.get("pipeline_parallel", 8)),
                    dry_run=dry_run,
                    strict=True,
                )
                summary = builder.build_for_projects([project], include_releases=True)
                for repo in repos:
                    task = task_by_key[(repo.source_key, "inventory", "pipelines")]
                    expected_digest = str(
                        task.metadata.get("inventory_digest", "")
                    )
                    snapshot = self.db.get_verified_pipeline_inventory_snapshot(
                        repo.ado_project,
                        repo.ado_repo,
                        expected_digest,
                    )
                    project_snapshots[repo.source_key] = snapshot
                    self._update_task(
                        run_id,
                        task,
                        "completed",
                        result={
                            **summary.get(project, {}),
                            "inventory_digest": snapshot.inventory_digest,
                        },
                    )
                snapshots.update(project_snapshots)
            except Exception as exc:
                for source_key in project_snapshots:
                    snapshots.pop(source_key, None)
                for repo in repos:
                    errors[repo.source_key] = str(exc)
                    task = task_by_key[(repo.source_key, "inventory", "pipelines")]
                    self._update_task(run_id, task, "failed", error=str(exc))
        return errors, snapshots

    def _record_scope_results(
        self,
        run_id: str,
        source_key: str,
        result: dict[str, Any],
        task_by_key: dict[tuple[str, str, str], PlanTask],
    ) -> None:
        for scope, scope_result in result.get("scopes", {}).items():
            task = task_by_key.get((source_key, "execute", scope))
            if not task:
                continue
            state = scope_result.get("status", "failed")
            task_status = {
                "completed": "completed",
                "needs_review": "needs_review",
                "failed": "failed",
            }.get(state, state)
            self._update_task(
                run_id,
                task,
                task_status,
                result=scope_result.get("detail", {}),
                error=scope_result.get("error", ""),
            )

    def _update_task(
        self,
        run_id: str,
        task: PlanTask,
        status: str,
        result: Any = None,
        error: str = "",
    ) -> None:
        task_id = self._db_task_id(run_id, task)
        row = self.db.get_pev_task(task_id)
        if not row:
            raise RuntimeError(f"PEV task {task_id} disappeared before update")
        expected_owner = getattr(self, "_task_lease_owner", None)
        if row.get("lease_owner") != expected_owner:
            # A completed task encountered during normal resume is already an
            # immutable receipt; reporting the same terminal status is an
            # idempotent no-op. Every other transition requires the exact
            # active worker lease and must fail closed on takeover.
            if row.get("status") == status and status in {
                "completed", "failed", "cancelled", "blocked", "skipped",
                "needs_review",
            }:
                return
            raise RuntimeError(
                f"PEV task lease was lost for {task_id}; stale update blocked"
            )
        if not self.db.update_pev_task(
            task_id,
            status=status,
            result=result,
            error=error,
            lease_owner=expected_owner,
            expected_status="in_progress",
        ):
            raise RuntimeError(
                f"Could not transition leased PEV task {task_id} to {status}"
            )
