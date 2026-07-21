"""Evidence-backed validator for an exact approved migration plan."""
from __future__ import annotations

from contextlib import contextmanager
import json
from datetime import datetime, timezone
import threading
from typing import Any, Optional
from uuid import uuid4

from ado2gh.pev.contracts import (
    MigrationPlan,
    PlanTask,
    ValidationReport,
    content_digest,
)
from ado2gh.pev.executor import PEVExecutor, plan_wave_id
from ado2gh.pev.planner import verify_plan_runtime_context
from ado2gh.pev.source_integrity import (
    fetch_project_access_snapshots,
    fetch_project_work_item_payloads,
)
from ado2gh.reporting.post_migration_validator import FAIL, PASS, WARN, PostMigrationValidator


_REPAIRABLE_CHECKS = {
    "head_commit": "repo",
    "branches": "repo",
    "tags": "repo",
}

# Only statuses emitted after a live executor attempt are valid inputs to the
# validator.  In particular, a planned/in-progress run or isolated dry run must
# never be promoted to completed merely because the remote repository happens
# to look healthy.
_EXECUTION_TERMINAL_STATUSES = frozenset({
    "executed",
    "failed",
    "needs_review",
    "completed",
    "validation_failed",
})
_REQUIRED_EXECUTION_TASK_KINDS = frozenset({"preflight", "inventory", "execute"})


class PEVValidator:
    def __init__(
        self,
        ado: Any,
        gh: Any,
        db: Any,
        global_cfg: Optional[dict[str, Any]] = None,
    ):
        self.ado = ado
        self.gh = gh
        self.db = db
        self.cfg = dict(global_cfg) if global_cfg is not None else None

    def validate(
        self,
        plan: MigrationPlan,
        run_id: str,
        output_path: Optional[str] = None,
        max_workers: int = 6,
    ) -> ValidationReport:
        plan.validate()
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
        if self.cfg is not None:
            verify_plan_runtime_context(plan, self.cfg, self.ado, self.gh)
        # Authenticate the run before acquiring its target locks.  Validation
        # then holds those locks for the complete remote evidence read.
        self._load_execution_state(plan, run_id)
        with self._target_lease_guard(plan, run_id) as lease_lost:
            return self._validate_fenced(
                plan,
                run_id,
                output_path=output_path,
                max_workers=max_workers,
                lease_lost=lease_lost,
            )

    @contextmanager
    def _target_lease_guard(self, plan: MigrationPlan, run_id: str):
        ttl_seconds = 300
        owner = f"validator_{uuid4().hex}"
        targets = [(repo.gh_org, repo.gh_repo) for repo in plan.repositories]
        tokens = self.db.acquire_pev_target_leases(
            plan.plan_id,
            run_id,
            owner,
            targets,
            ttl_seconds=ttl_seconds,
        )
        if tokens is None:
            raise RuntimeError(
                "Validation cannot start because an approved target is being "
                "modified by another plan"
            )
        stopped = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            interval = max(10.0, ttl_seconds / 3)
            while not stopped.wait(interval):
                try:
                    renewed = self.db.renew_pev_target_leases(
                        plan.plan_id,
                        run_id,
                        owner,
                        tokens,
                        ttl_seconds=ttl_seconds,
                    )
                except Exception:
                    lost.set()
                    return
                if not renewed:
                    lost.set()
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"ado2gh-validator-lease-{run_id}",
            daemon=True,
        )
        thread.start()
        try:
            yield lost
            if lost.is_set():
                raise RuntimeError(
                    "Target lease was lost during validation; evidence was discarded"
                )
        finally:
            stopped.set()
            thread.join(timeout=5)
            self.db.release_pev_target_leases(
                plan.plan_id,
                run_id,
                owner,
                tokens,
            )

    @staticmethod
    def _require_validation_lease(lease_lost: threading.Event) -> None:
        if lease_lost.is_set():
            raise RuntimeError(
                "Target lease was lost during validation; evidence write blocked"
            )

    def _validate_fenced(
        self,
        plan: MigrationPlan,
        run_id: str,
        output_path: Optional[str] = None,
        max_workers: int = 6,
        lease_lost: Optional[threading.Event] = None,
    ) -> ValidationReport:
        plan.validate()
        lease_lost = lease_lost or threading.Event()
        run, task_rows, planned_tasks_by_db_id = self._load_execution_state(
            plan, run_id
        )
        source_inventory_before = self._verify_source_inventory(plan)

        approved_source_snapshots = {
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
        # Pipeline validation must use the inventory receipts which were part
        # of the content-addressed plan.  Reading the current inventory here
        # would let a later rescan silently change the validator's denominator.
        approved_pipeline_snapshots = {
            task.source_key: dict(task.metadata)
            for task in plan.tasks
            if task.kind == "inventory"
            and task.scope == "pipelines"
        }
        approved_scope_snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        for task in plan.tasks:
            if task.kind != "execute" or "source_snapshot" not in task.metadata:
                continue
            approved_scope_snapshots.setdefault(task.source_key, {})[
                task.scope
            ] = dict(task.metadata["source_snapshot"])
        approved_target_snapshots = {
            task.source_key: dict(task.metadata)
            for task in plan.tasks
            if task.kind == "preflight"
        }
        delivery_policy = plan.policy.get("pipeline_delivery", {})
        approved_staging_branch = ""
        if isinstance(delivery_policy, dict) and delivery_policy.get(
            "mode"
        ) == "pull_request":
            approved_staging_branch = str(delivery_policy.get("branch", ""))
        validator = PostMigrationValidator(
            self.ado,
            self.gh,
            self.db,
            approved_source_snapshots,
            approved_pipeline_snapshots,
            plan_wave_id(plan),
            approved_staging_branch,
            plan.plan_id,
            run_id,
            approved_scope_snapshots,
        )
        # Set after construction so embedders/test doubles implementing the
        # long-standing validator constructor remain compatible. The bundled
        # validator also accepts this at construction, but reads the same
        # immutable mapping from this attribute during validation.
        validator.approved_target_snapshots = approved_target_snapshots
        validator.approved_access_snapshots = {
            repo.source_key: dict(repo.source_access_snapshot)
            for repo in plan.repositories
        }
        validator.live_access_snapshots = (
            fetch_project_access_snapshots(self.ado, plan.repositories)
            if callable(getattr(self.ado, "list_project_git_acls", None))
            else {}
        )
        source_selection = plan.policy.get("source_selection", {})
        validator.include_unlinked_work_items = bool(
            source_selection.get("include_unlinked_work_items", False)
            if isinstance(source_selection, dict) else False
        )
        work_item_repos = [
            repo for repo in plan.repositories
            if "work_items" in repo.scopes
        ]
        validator.work_item_payloads = (
            fetch_project_work_item_payloads(
                self.ado,
                work_item_repos,
                include_unlinked_work_items=validator.include_unlinked_work_items,
            )
            if work_item_repos else {}
        )
        observed = validator.validate(
            [repo.to_repo_config() for repo in plan.repositories],
            output_path=output_path,
            max_workers=max_workers,
        )
        source_inventory_after = self._verify_source_inventory(plan)
        if source_inventory_before != source_inventory_after:
            raise RuntimeError(
                "ADO organization repository inventory changed during validation"
            )
        for item in observed:
            item.setdefault("checks", {})["source_inventory"] = {
                "verdict": "PASS",
                **source_inventory_after,
            }
        observed_by_source = {
            f"{item.get('ado_project', '')}/{item.get('ado_repo', '')}": item
            for item in observed
        }
        task_status_by_source: dict[str, list[tuple[PlanTask, str]]] = {}
        for row in task_rows:
            task = planned_tasks_by_db_id[row["task_id"]]
            if task.kind in _REQUIRED_EXECUTION_TASK_KINDS:
                task_status_by_source.setdefault(task.source_key, []).append(
                    (task, str(row.get("status", "")))
                )

        failures: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        validated_at = datetime.now(timezone.utc).isoformat()
        validate_tasks = {
            task.source_key: task for task in plan.tasks if task.kind == "validate"
        }

        for repo in plan.repositories:
            source_key = repo.source_key
            failure_count_before = len(failures)
            warning_count_before = len(warnings)
            result = observed_by_source.get(source_key)
            if result is None:
                result = {
                    "ado_project": repo.ado_project,
                    "ado_repo": repo.ado_repo,
                    "gh_target": repo.target_key,
                    "overall": FAIL,
                    "checks": {},
                    "error": "Validator returned no result for planned repository",
                }
                observed.append(result)

            if result.get("gh_target") != repo.target_key:
                result["overall"] = FAIL
                result.setdefault("checks", {})["mapping"] = {
                    "verdict": FAIL,
                    "detail": (
                        f"Observed target {result.get('gh_target')} does not match "
                        f"approved target {repo.target_key}"
                    ),
                }

            for category, check in result.get("checks", {}).items():
                verdict = check.get("verdict", FAIL)
                evidence = {
                    "source": source_key,
                    "target": repo.target_key,
                    "category": category,
                    "check": check,
                    "validated_at": validated_at,
                }
                validate_task = validate_tasks[source_key]
                self._require_validation_lease(lease_lost)
                self.db.record_validation_evidence(
                    run_id,
                    category,
                    verdict.lower(),
                    evidence=evidence,
                    task_id=PEVExecutor._db_task_id(run_id, validate_task),
                )
                item = {
                    "source_key": source_key,
                    "target_key": repo.target_key,
                    "category": category,
                    "detail": check.get("detail", ""),
                    "scope": _REPAIRABLE_CHECKS.get(category, ""),
                    "retryable": category in _REPAIRABLE_CHECKS,
                }
                if verdict == FAIL:
                    failures.append(item)
                elif verdict == WARN:
                    warnings.append(item)

            if result.get("overall") == FAIL and len(failures) == failure_count_before:
                failures.append({
                    "source_key": source_key,
                    "target_key": repo.target_key,
                    "category": "validator",
                    "detail": result.get("error", "Repository validation failed"),
                    "scope": "",
                    "retryable": False,
                })
            elif result.get("overall") == WARN and len(warnings) == warning_count_before:
                warnings.append({
                    "source_key": source_key,
                    "target_key": repo.target_key,
                    "category": "validator",
                    "detail": result.get("error", "Repository validation needs review"),
                    "scope": "",
                    "retryable": False,
                })

            execution_states = task_status_by_source.get(source_key, [])
            invalid_states = [
                (task, state)
                for task, state in execution_states
                if state not in {"completed", "needs_review"}
            ]
            review_states = [
                (task, state)
                for task, state in execution_states
                if state == "needs_review"
            ]
            if invalid_states:
                state_detail = ", ".join(
                    self._task_state_label(task, state)
                    for task, state in invalid_states
                )
                failures.append({
                    "source_key": source_key,
                    "target_key": repo.target_key,
                    "category": "execution",
                    "detail": (
                        "Approved execution tasks did not complete successfully: "
                        f"{state_detail}"
                    ),
                    "scope": "",
                    "retryable": False,
                })
            elif review_states:
                state_detail = ", ".join(
                    self._task_state_label(task, state)
                    for task, state in review_states
                )
                warnings.append({
                    "source_key": source_key,
                    "target_key": repo.target_key,
                    "category": "execution_review",
                    "detail": (
                        "Approved execution tasks require human review: "
                        f"{state_detail}"
                    ),
                    "scope": "",
                    "retryable": False,
                })

            repo_failed = len(failures) > failure_count_before
            repo_warned = len(warnings) > warning_count_before
            task_status = "failed" if repo_failed else (
                "needs_review" if repo_warned else "completed"
            )
            validate_task = validate_tasks[source_key]
            self._require_validation_lease(lease_lost)
            self.db.update_pev_task(
                PEVExecutor._db_task_id(run_id, validate_task),
                status=task_status,
                result=result,
                error=result.get("error", ""),
            )

        # A run-level terminal state is an independent execution receipt.  Do
        # not let inconsistent state (for example, a failed run whose task rows
        # were all marked completed) be upgraded by remote validation alone.
        if run["status"] == "failed":
            failures.append({
                "source_key": "",
                "target_key": "",
                "category": "execution_run",
                "detail": "The approved execution run ended in failed state",
                "scope": "",
                "retryable": False,
            })
        elif run["status"] == "needs_review":
            warnings.append({
                "source_key": "",
                "target_key": "",
                "category": "execution_run_review",
                "detail": "The approved execution run requires human review",
                "scope": "",
                "retryable": False,
            })

        # De-duplicate failures emitted by both execution and check evidence.
        failures = self._unique(failures)
        warnings = self._unique(warnings)
        status = "failed" if failures else ("needs_review" if warnings else "passed")
        summary = {
            "validation_status": status,
            "repositories": len(plan.repositories),
            "failures": len(failures),
            "warnings": len(warnings),
            "validated_at": validated_at,
        }
        self._require_validation_lease(lease_lost)
        self.db.upsert_pev_run(
            run_id,
            plan.plan_id,
            status={
                "passed": "completed",
                "needs_review": "needs_review",
                "failed": "validation_failed",
            }[status],
            config_digest=plan.config_digest,
            summary=summary,
        )
        return ValidationReport(
            run_id=run_id,
            plan_id=plan.plan_id,
            status=status,
            repository_results=sorted(
                observed,
                key=lambda item: (
                    item.get("ado_project", ""), item.get("ado_repo", "")
                ),
            ),
            failures=failures,
            warnings=warnings,
            validated_at=validated_at,
        )

    def _verify_source_inventory(self, plan: MigrationPlan) -> dict[str, Any]:
        receipt = plan.policy.get("source_inventory", {})
        mode = receipt.get("mode") if isinstance(receipt, dict) else None
        if mode == "explicit_subset":
            return {
                "mode": "explicit_subset",
                "repository_count": len(plan.repositories),
                "digest": receipt.get("digest", ""),
                "detail": "Validation is scoped to an explicit repository subset",
            }
        if mode != "organization_snapshot":
            raise RuntimeError("Approved source organization inventory is invalid")
        include_disabled = bool(receipt.get("include_disabled", False))
        rows: list[dict[str, str]] = []
        projects = self.ado.list_projects()
        if not isinstance(projects, list):
            projects = list(projects)
        for project in projects:
            if not isinstance(project, dict):
                raise RuntimeError("ADO project inventory item is malformed")
            project_name = str(project.get("name") or "").strip()
            if not project_name:
                raise RuntimeError("ADO project inventory contains an empty name")
            repositories = self.ado.list_repos(project_name)
            if not isinstance(repositories, list):
                repositories = list(repositories)
            for repository in repositories:
                if not isinstance(repository, dict):
                    raise RuntimeError("ADO repository inventory item is malformed")
                if repository.get("isDisabled", False) and not include_disabled:
                    continue
                name = str(repository.get("name") or "").strip()
                repo_id = str(repository.get("id") or "").strip()
                if not name or not repo_id:
                    raise RuntimeError(
                        "ADO repository inventory identity is incomplete"
                    )
                rows.append({
                    "source_key": f"{project_name}/{name}",
                    "source_repo_id": repo_id,
                })
        rows.sort(key=lambda item: item["source_key"].casefold())
        digest = content_digest(rows)
        if (
            len(rows) != receipt.get("repository_count")
            or digest != receipt.get("digest")
        ):
            raise RuntimeError(
                "ADO organization repository inventory changed after planning; "
                "freeze source creation/deletion and approve a new plan"
            )
        return {
            "mode": mode,
            "repository_count": len(rows),
            "digest": digest,
            "detail": "Complete ADO organization repository inventory is unchanged",
        }

    def _load_execution_state(
        self,
        plan: MigrationPlan,
        run_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, PlanTask]]:
        """Authenticate the durable execution receipt before remote checks.

        Plan validation authenticates the in-memory artifact.  This method
        independently reconstructs every persisted task identity so a missing,
        injected, or altered row cannot be hidden by successful remote checks.
        """
        run = self.db.get_pev_run(run_id)
        if not run or run.get("plan_id") != plan.plan_id:
            raise ValueError(f"Run {run_id!r} is not an execution of {plan.plan_id}")
        if run.get("config_digest") != plan.config_digest:
            raise ValueError(
                f"Run {run_id!r} configuration does not match approved plan "
                f"{plan.plan_id}"
            )
        run_status = str(run.get("status", ""))
        if run_status not in _EXECUTION_TERMINAL_STATUSES:
            raise ValueError(
                f"Run {run_id!r} is not ready for validation: execution status "
                f"is {run_status!r}"
            )

        task_rows = self.db.list_pev_tasks(run_id)
        planned_tasks_by_db_id = {
            PEVExecutor._db_task_id(run_id, task): task for task in plan.tasks
        }
        persisted_by_id = {str(row.get("task_id", "")): row for row in task_rows}
        expected_ids = set(planned_tasks_by_db_id)
        observed_ids = set(persisted_by_id)
        if expected_ids != observed_ids:
            missing = sorted(expected_ids - observed_ids)
            extra = sorted(observed_ids - expected_ids)
            raise ValueError(
                f"Run {run_id!r} task graph does not match approved plan: "
                f"missing={missing}, extra={extra}"
            )

        for task_id, task in planned_tasks_by_db_id.items():
            row = persisted_by_id[task_id]
            expected = {
                "run_id": run_id,
                "kind": task.kind,
                "source_ref": task.source_key,
                "target_ref": task.target_key,
                "input_digest": content_digest(task.to_dict()),
                "idempotency_key": f"{plan.plan_id}:{task.task_id}",
                "strategy": task.execution_mode,
            }
            mismatches = [
                field
                for field, value in expected.items()
                if row.get(field) != value
            ]
            try:
                dependencies = json.loads(str(row.get("dependency_ids", "")))
            except (TypeError, ValueError, json.JSONDecodeError):
                dependencies = None
            if dependencies != list(task.dependencies):
                mismatches.append("dependency_ids")
            if mismatches:
                raise ValueError(
                    f"Run {run_id!r} task {task_id!r} does not match approved "
                    f"plan fields: {sorted(set(mismatches))}"
                )

        return run, task_rows, planned_tasks_by_db_id

    @staticmethod
    def _task_state_label(task: PlanTask, state: str) -> str:
        scope = f"/{task.scope}" if task.scope else ""
        return f"{task.kind}{scope}={state or '<empty>'}"

    @staticmethod
    def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        result: list[dict[str, Any]] = []
        for item in items:
            key = (
                item.get("source_key", ""),
                item.get("category", ""),
                item.get("detail", ""),
            )
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
