"""Pipeline transform scope."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, MigrationStatus, PipelineMetadata, RepoConfig
from ado2gh.output_dirs import output_base
from ado2gh.pipelines.push_workflows import push_repo_workflows, remote_workflow_files
from ado2gh.pipelines.resolve.template_resolver import make_ado_git_fetcher
from ado2gh.pipelines.transform import PipelineTransformer
from ado2gh.pipelines.validation import WorkflowValidator

DEFAULT_WORKFLOW_BRANCH = "ado2gh/migrated-workflows"


def _workflow_branch(ctx: ScopeContext) -> str:
    return ctx.global_cfg.get("workflow_branch") or DEFAULT_WORKFLOW_BRANCH


def _finish_with_push(
    stats: dict[str, Any],
    ctx: ScopeContext,
    repo: RepoConfig,
    workflow_branch: str,
) -> ScopeResult:
    push = push_repo_workflows(
        ctx.gh,
        repo,
        str(output_base() / "workflows"),
        branch=workflow_branch,
        dry_run=False,
        readiness_ok=True,
        approver_ok=True,
    )
    stats["workflow_branch"] = workflow_branch
    stats["workflow_files"] = push.get("workflow_files") or []
    stats["pr_url"] = push.get("pr_url") or ""
    stats["workflows_pushed"] = bool(push.get("pushed"))

    if not push.get("pushed"):
        err = push.get("error") or "workflow push failed"
        stats["push_error"] = err
        completed = stats.get("completed", 0)
        stats["message"] = (
            f"Generated {completed} workflow file(s) locally but "
            f"did not push to GitHub: {err}"
        )
        return ScopeResult(stats=stats, failed=max(1, completed))

    pr_note = f" PR: {stats['pr_url']}" if stats["pr_url"] else ""
    stats["message"] = (
        f"Pushed {len(stats['workflow_files'])} workflow(s) to branch "
        f"`{workflow_branch}` on {repo.gh_org}/{repo.gh_repo}.{pr_note}"
    )
    return ScopeResult(stats=stats, failed=0)


class PipelinesScopeHandler:
    scope = MigrationScope.PIPELINES.value

    def __init__(self):
        self.transformer = PipelineTransformer()
        self.validator = WorkflowValidator()

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        pipeline_parallel = kwargs.get("pipeline_parallel", ctx.pipeline_parallel)
        wave_id = kwargs.get("wave_id", ctx.wave_id)
        workflow_branch = _workflow_branch(ctx)

        pipelines = ctx.db.get_pipelines_for_repo(repo.ado_project, repo.ado_repo)
        if repo.pipeline_filter:
            pat = re.compile(repo.pipeline_filter)
            pipelines = [p for p in pipelines if pat.search(p.pipeline_name)]

        stats: dict[str, Any] = {
            "total": len(pipelines),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "warnings": [],
        }
        if not pipelines:
            stats["message"] = (
                "No ADO pipelines linked to this repo in inventory — "
                "run pipeline inventory and verify repo association"
            )
            return ScopeResult(stats=stats, failed=1)

        env_names: set[str] = set()
        for pipe in pipelines:
            for env in pipe.environments:
                env_names.add(env.name)
            for stage in pipe.stages:
                if stage.environment:
                    env_names.add(stage.environment.name)

        if not ctx.dry_run:
            for env_name in sorted(env_names):
                try:
                    ctx.gh.create_environment(repo.gh_org, repo.gh_repo, env_name)
                except Exception as exc:
                    log.warning("env create %s failed: %s", env_name, exc)

        existing = ctx.db.get_wave_pipeline_migrations(wave_id)
        completed_ids = {
            r["pipeline_id"] for r in existing
            if r["status"] == MigrationStatus.COMPLETED.value
               and r["project"] == repo.ado_project
               and r["gh_org"] == repo.gh_org
               and r["gh_repo"] == repo.gh_repo
        }
        pending = [p for p in pipelines if p.pipeline_id not in completed_ids]
        stats["skipped"] = len(pipelines) - len(pending)

        output_root = (
            output_base() / "workflows" / repo.gh_org / repo.gh_repo
            / ".github" / "workflows"
        )
        stats["output_dir"] = str(output_root)

        if ctx.dry_run:
            # Dry-run: transform and validate locally without pushing
            stats["dry_run"] = True
            stats["validation_mode"] = self.validator.validation_mode
            validation_passed = 0
            validation_failed = 0
            validation_errors: list[str] = []

            for pipe in pending:
                try:
                    fetch_template = make_ado_git_fetcher(
                        ctx.ado,
                        pipe.project,
                        pipe.repo_id,
                        pipe.repo_branch,
                        pipe.yaml_path,
                    )
                    result = self.transformer.transform(
                        pipe, output_root, fetch_template=fetch_template,
                    )
                    workflow_file = result.get("workflow_file")
                    if workflow_file:
                        validation = self.validator.validate(workflow_file)
                        if validation.validation_status == "valid":
                            validation_passed += 1
                        else:
                            validation_failed += 1
                            validation_errors.extend(validation.validation_errors)
                        stats["completed"] += 1
                    else:
                        stats["failed"] += 1
                        validation_errors.append(f"No workflow file generated for {pipe.pipeline_name}")
                except Exception as exc:
                    stats["failed"] += 1
                    validation_errors.append(f"{pipe.pipeline_name}: {exc}")

            stats["validation_passed"] = validation_passed
            stats["validation_failed"] = validation_failed
            stats["validation_errors"] = validation_errors[:10]  # First 10 errors

            if validation_failed > 0:
                stats["message"] = (
                    f"Dry-run: transformed {len(pending)} pipeline(s), "
                    f"{validation_passed} validated, {validation_failed} failed validation"
                )
                return ScopeResult(stats=stats, failed=validation_failed)
            else:
                stats["message"] = (
                    f"Dry-run: transformed and validated {len(pending)} pipeline(s) successfully"
                )
                return ScopeResult(stats=stats)

        remote_files = remote_workflow_files(ctx.gh, repo, workflow_branch)
        if not pending and remote_files:
            stats["workflow_branch"] = workflow_branch
            stats["workflow_files"] = remote_files
            stats["workflows_pushed"] = True
            stats["message"] = (
                f"All pipelines already transformed; {len(remote_files)} workflow(s) on "
                f"branch `{workflow_branch}`"
            )
            return ScopeResult(stats=stats)

        if not pending and not remote_files:
            push = push_repo_workflows(
                ctx.gh,
                repo,
                str(output_base() / "workflows"),
                branch=workflow_branch,
                dry_run=False,
                readiness_ok=True,
                approver_ok=True,
            )
            if push.get("pushed"):
                stats["workflow_branch"] = workflow_branch
                stats["workflow_files"] = push.get("workflow_files") or []
                stats["pr_url"] = push.get("pr_url") or ""
                stats["workflows_pushed"] = True
                pr_note = f" PR: {stats['pr_url']}" if stats["pr_url"] else ""
                stats["message"] = (
                    f"Pushed {len(stats['workflow_files'])} workflow(s) to branch "
                    f"`{workflow_branch}` on {repo.gh_org}/{repo.gh_repo}.{pr_note}"
                )
                return ScopeResult(stats=stats)

            pending = list(pipelines)
            stats["skipped"] = 0
            stats["warnings"].append(
                "Prior transform recorded in state but workflows missing on GitHub — "
                "re-transforming and pushing"
            )

        cm = kwargs.get("concurrency")
        workers = min(pipeline_parallel, max(1, len(pending)))

        def _transform_one(pipe: PipelineMetadata) -> dict:
            if cm:
                with cm.pipeline_slot():
                    return self._do_transform(
                        pipe, wave_id, repo, ctx, output_root,
                    )
            return self._do_transform(pipe, wave_id, repo, ctx, output_root)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_transform_one, p): p for p in pending}
            for fut in as_completed(futures):
                res = fut.result()
                if res["status"] == "completed":
                    stats["completed"] += 1
                else:
                    stats["failed"] += 1
                    stats["warnings"].append(res.get("error", "unknown"))

        if stats["failed"] > 0:
            stats["message"] = (
                f"Transformed {stats['completed']}/{stats['total']} pipeline(s); "
                f"{stats['failed']} failed"
            )
            return ScopeResult(stats=stats, failed=stats["failed"])

        return _finish_with_push(stats, ctx, repo, workflow_branch)

    def _do_transform(
        self, pipe: PipelineMetadata, wave_id: int, repo: RepoConfig,
        ctx: ScopeContext, output_root,
    ) -> dict:
        ctx.db.upsert_pipeline_migration(
            wave_id, pipe, repo.gh_org, repo.gh_repo, MigrationStatus.IN_PROGRESS,
        )
        try:
            fetch_template = make_ado_git_fetcher(
                ctx.ado,
                pipe.project,
                pipe.repo_id,
                pipe.repo_branch,
                pipe.yaml_path,
            )
            result = self.transformer.transform(
                pipe, output_root, fetch_template=fetch_template,
            )
            ctx.db.upsert_pipeline_migration(
                wave_id, pipe, repo.gh_org, repo.gh_repo,
                MigrationStatus.COMPLETED,
                workflow_file=str(result.get("workflow_file", "")),
                warnings=result.get("warnings", []),
                unsupported=result.get("unsupported_tasks", []),
                transform_stats=result.get("stats"),
            )
            return {"pipeline_id": pipe.pipeline_id, "status": "completed"}
        except Exception as exc:
            ctx.db.upsert_pipeline_migration(
                wave_id, pipe, repo.gh_org, repo.gh_repo,
                MigrationStatus.FAILED, error=str(exc),
            )
            return {"pipeline_id": pipe.pipeline_id, "status": "failed", "error": str(exc)}
