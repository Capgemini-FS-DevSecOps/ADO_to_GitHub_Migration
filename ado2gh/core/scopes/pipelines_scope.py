"""Pipeline transform scope."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, MigrationStatus, PipelineMetadata, RepoConfig
from ado2gh.output_dirs import output_base
from ado2gh.pipelines.transform import PipelineTransformer
from ado2gh.tools.push_workflows import push_repo_workflows

DEFAULT_WORKFLOW_BRANCH = "ado2gh/migrated-workflows"


class PipelinesScopeHandler:
    scope = MigrationScope.PIPELINES.value

    def __init__(self):
        self.transformer = PipelineTransformer()

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        pipeline_parallel = kwargs.get("pipeline_parallel", ctx.pipeline_parallel)
        wave_id = kwargs.get("wave_id", ctx.wave_id)

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
        }
        pending = [p for p in pipelines if p.pipeline_id not in completed_ids]
        stats["skipped"] = len(pipelines) - len(pending)

        output_root = (
            output_base() / "workflows" / repo.gh_org / repo.gh_repo
            / ".github" / "workflows"
        )
        stats["output_dir"] = str(output_root)

        if ctx.dry_run:
            stats["dry_run"] = True
            stats["would_transform"] = len(pending)
            stats["message"] = (
                f"Dry-run: would transform {len(pending)} pipeline(s) to "
                f"{output_root} (not pushed to GitHub)"
            )
            return ScopeResult(stats=stats)

        if not pending:
            stats["message"] = "All pipelines already transformed for this wave"
            return ScopeResult(stats=stats)

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

        workflow_branch = (
            ctx.global_cfg.get("workflow_branch")
            or DEFAULT_WORKFLOW_BRANCH
        )
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
            stats["message"] = (
                f"Generated {stats['completed']} workflow file(s) locally but "
                f"did not push to GitHub: {err}"
            )
            return ScopeResult(stats=stats, failed=max(1, stats["completed"]))

        pr_note = f" PR: {stats['pr_url']}" if stats["pr_url"] else ""
        stats["message"] = (
            f"Pushed {len(stats['workflow_files'])} workflow(s) to branch "
            f"`{workflow_branch}` on {repo.gh_org}/{repo.gh_repo}.{pr_note}"
        )
        return ScopeResult(stats=stats, failed=0)

    def _do_transform(
        self, pipe: PipelineMetadata, wave_id: int, repo: RepoConfig,
        ctx: ScopeContext, output_root,
    ) -> dict:
        ctx.db.upsert_pipeline_migration(
            wave_id, pipe, repo.gh_org, repo.gh_repo, MigrationStatus.IN_PROGRESS,
        )
        try:
            result = self.transformer.transform(pipe, output_root)
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
