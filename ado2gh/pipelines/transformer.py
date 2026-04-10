"""Transforms ADO PipelineMetadata into GitHub Actions workflow YAML."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ado2gh.models import (
    PipelineMetadata,
    PipelineType,
)

logger = logging.getLogger(__name__)

# ── ADO task -> GitHub Actions action mapping ────────────────────────────────

ADO_TASK_MAP: dict[str, str] = {
    # Setup / tool-installer tasks
    "NodeTool@0":              "actions/setup-node@v4",
    "UsePythonVersion@0":      "actions/setup-python@v5",
    "DotNetCoreCLI@2":         "actions/setup-dotnet@v4",
    "JavaToolInstaller@0":     "actions/setup-java@v4",
    "GoTool@0":                "actions/setup-go@v5",
    # Docker
    "Docker@2":                "docker/build-push-action@v5",
    # Azure tasks
    "AzureCLI@2":              "azure/CLI@v2",
    "AzureWebApp@1":           "azure/webapps-deploy@v3",
    "AzureFunctionApp@2":      "azure/functions-action@v1",
    # Artifact tasks
    "PublishBuildArtifacts@1":  "actions/upload-artifact@v4",
    "DownloadBuildArtifacts@0": "actions/download-artifact@v4",
    # Test reporting
    "PublishTestResults@2":     "dorny/test-reporter@v1",
    # Build / package tasks (mapped to run steps with notes)
    "NuGetCommand@2":          "run",
    "Maven@4":                 "run",
    "Gradle@3":                "run",
    "Terraform@0":             "run",
    "HelmDeploy@0":            "run",
    "Kubernetes@1":            "run",
    # Script / shell tasks (always become run steps)
    "CmdLine@2":               "run",
    "Bash@3":                  "run",
    "PowerShell@2":            "run",
    "Npm@1":                   "run",
    "PipAuthenticate@1":       "run",
    "NpmAuthenticate@0":       "run",
}

# Tasks that map to plain ``run:`` steps rather than ``uses:`` steps.
_RUN_BASED_TASKS: set[str] = {
    k for k, v in ADO_TASK_MAP.items() if v == "run"
}

# ADO agent-pool name fragments -> GitHub-hosted runner labels.
_POOL_RUNNER_MAP: dict[str, str] = {
    "ubuntu":       "ubuntu-latest",
    "windows":      "windows-latest",
    "macos":        "macos-latest",
    "hosted":       "ubuntu-latest",
    "default":      "ubuntu-latest",
    "azure pipelines": "ubuntu-latest",
}

# Day-of-week bits used by ADO cron schedules (Sunday = 1 … Saturday = 64).
_DOW_NAMES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
_DOW_BITS  = [1, 2, 4, 8, 16, 32, 64]


class PipelineTransformer:
    """Convert an ADO ``PipelineMetadata`` object into a GitHub Actions workflow.

    Agent-friendly design:
    - `transform_to_spec()` produces a pure(ish) spec (dict + warnings + notes).
    - `render_workflow_yaml()` serializes spec.workflow to YAML.
    - `write_spec()` writes workflow + notes to disk.

    The legacy `transform()` API is preserved for compatibility.
    """

    # ── agent-friendly public API ─────────────────────────────────────────

    def transform_to_spec(self, meta: PipelineMetadata):
        """Build a workflow spec and notes content from PipelineMetadata.

        Returns an instance compatible with `ado2gh.tools.pipeline_migration.PipelineTransformSpec`.
        """
        warnings: list[str] = []
        unsupported: list[str] = list(meta.unsupported_tasks)

        if meta.pipeline_type == PipelineType.YAML:
            workflow = self._build_yaml_workflow(meta, warnings, unsupported)
        elif meta.pipeline_type == PipelineType.RELEASE:
            workflow = self._build_release_workflow(meta, warnings, unsupported)
        else:
            workflow = self._build_classic_workflow(meta, warnings, unsupported)

        metrics = self._compute_metrics(workflow)
        notes_md = self.render_notes_markdown(meta, warnings, unsupported)

        # Local import to avoid a hard dependency loop.
        from ado2gh.tools.pipeline_migration import PipelineTransformSpec

        return PipelineTransformSpec(
            workflow=workflow,
            warnings=warnings,
            unsupported_tasks=unsupported,
            notes_markdown=notes_md,
            metrics=metrics,
        )

    @staticmethod
    def render_workflow_yaml(*, meta: PipelineMetadata, workflow: dict[str, Any]) -> str:
        """Serialize a workflow dict to YAML text with a standard header."""
        header = (
            f"# ---------------------------------------------------------\n"
            f"# Auto-generated GitHub Actions workflow\n"
            f"# Source: ADO pipeline '{meta.pipeline_name}' "
            f"(id={meta.pipeline_id}, type={meta.pipeline_type.value})\n"
            f"# Project: {meta.project}  Repo: {meta.repo_name}\n"
            f"# Generated: {datetime.now(timezone.utc).isoformat()}\n"
            f"# ---------------------------------------------------------\n\n"
        )
        return header + yaml.dump(
            workflow,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )

    @staticmethod
    def render_notes_markdown(
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> str:
        """Render migration notes as markdown text."""
        lines: list[str] = [
            f"# Migration Notes – {meta.pipeline_name}",
            "",
            "## Pipeline Information",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Pipeline ID | {meta.pipeline_id} |",
            f"| Pipeline Name | {meta.pipeline_name} |",
            f"| Type | {meta.pipeline_type.value} |",
            f"| Project | {meta.project} |",
            f"| Repository | {meta.repo_name} |",
            f"| Complexity | {meta.complexity.value} |",
            f"| Avg Duration | {meta.avg_duration_min:.1f} min |",
            f"| Runs (last 30 days) | {meta.total_runs_30d} |",
            "",
        ]

        if meta.service_connections:
            lines += [
                "## Service Connections",
                "",
                "The following ADO service connections must be replaced with GitHub secrets or OIDC federation:",
                "",
            ]
            for sc in meta.service_connections:
                sc_name = sc.get("name", sc) if isinstance(sc, dict) else str(sc)
                lines.append(f"- `{sc_name}`")
            lines.append("")

        if meta.variable_groups:
            lines += [
                "## Variable Groups",
                "",
                "ADO variable groups must be migrated to GitHub Actions secrets / variables or environment-level settings:",
                "",
            ]
            for vg in meta.variable_groups:
                vg_name = vg.get("name", vg) if isinstance(vg, dict) else str(vg)
                lines.append(f"- `{vg_name}`")
            lines.append("")

        if meta.environments:
            lines += [
                "## Environments",
                "",
                "Create matching GitHub environments with appropriate protection rules:",
                "",
            ]
            for env in meta.environments:
                approvers = ", ".join(env.required_approvers) or "none"
                lines.append(
                    f"- **{env.name}** – approvers: {approvers}, timeout: {env.approval_timeout_min} min"
                )
            lines.append("")

        if unsupported:
            lines += [
                "## Unsupported Tasks",
                "",
                "The following ADO tasks have no direct GitHub Actions equivalent and require manual migration:",
                "",
            ]
            for task in sorted(set(unsupported)):
                lines.append(f"- `{task}`")
            lines.append("")

        if warnings:
            lines += ["## Warnings", ""]
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

        if meta.migration_notes:
            lines += ["## Additional Notes", ""]
            for note in meta.migration_notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

    def write_spec(self, spec, *, output_dir: Path, file_stem: str) -> tuple[Path, Path]:
        """Write workflow YAML + notes markdown to output_dir.

        Returns: (workflow_path, notes_path)
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # spec.workflow is in-memory; spec.notes_markdown is already rendered.
        # We need meta for the YAML header; the notes already include it.
        # So we accept meta not here; header omitted if unknown.
        # Callers should prefer `transform()` or use `render_workflow_yaml(meta=..., ...)`.

        workflow_file = output_dir / f"{file_stem}.yml"
        notes_file = output_dir / f"{file_stem}_migration_notes.md"

        # Best-effort YAML rendering without header if meta isn't provided.
        yaml_text = yaml.dump(
            spec.workflow,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )
        workflow_file.write_text(yaml_text, encoding="utf-8")
        notes_file.write_text(spec.notes_markdown, encoding="utf-8")
        return workflow_file, notes_file

    @staticmethod
    def _compute_metrics(workflow: dict[str, Any]) -> dict[str, Any]:
        jobs = workflow.get("jobs") or {}
        job_count = len(jobs) if isinstance(jobs, dict) else 0
        step_count = 0
        if isinstance(jobs, dict):
            for j in jobs.values():
                steps = j.get("steps") if isinstance(j, dict) else None
                if isinstance(steps, list):
                    step_count += len(steps)
        return {"job_count": job_count, "step_count": step_count}

    # ── legacy public API (compatible) ─────────────────────────────────────

    def transform(self, meta: PipelineMetadata, output_dir: Path) -> dict[str, Any]:
        """Legacy entry-point: generates workflow + notes files.

        Kept for compatibility with existing CLI workflows.
        Returns JSON-serializable paths as strings.
        """
        output_dir = Path(output_dir)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", meta.pipeline_name).lower()

        spec = self.transform_to_spec(meta)

        yaml_text = self.render_workflow_yaml(meta=meta, workflow=spec.workflow)
        workflow_file = output_dir / f"{safe_name}.yml"
        output_dir.mkdir(parents=True, exist_ok=True)
        workflow_file.write_text(yaml_text, encoding="utf-8")

        notes_file = output_dir / f"{safe_name}_migration_notes.md"
        notes_file.write_text(spec.notes_markdown, encoding="utf-8")

        logger.info(
            "Transformed pipeline %s (%s) -> %s  (%d warnings, %d unsupported)",
            meta.pipeline_name,
            meta.pipeline_type.value,
            workflow_file,
            len(spec.warnings),
            len(spec.unsupported_tasks),
        )

        return {
            "workflow_file": str(workflow_file),
            "notes_file": str(notes_file),
            "warnings": spec.warnings,
            "unsupported_tasks": spec.unsupported_tasks,
            "metrics": spec.metrics,
        }

    def transform_many(self, meta: PipelineMetadata, output_dir: Path) -> dict[str, Any]:
        """Generate one workflow per template plus the root workflow.

        Output naming:
        - root: <pipeline_name>.yml
        - templates: <pipeline_name>__<template_stem>.yml

        Returns a dict with `workflow_files` list.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_root = re.sub(r"[^a-zA-Z0-9_-]", "_", meta.pipeline_name).lower()

        results: dict[str, Any] = {"workflow_files": [], "notes_files": []}

        # Root workflow uses resolved YAML if present.
        root_result = self.transform(meta, output_dir)
        results["workflow_files"].append(root_result["workflow_file"])
        results["notes_files"].append(root_result["notes_file"])
        results["warnings"] = root_result.get("warnings", [])
        results["unsupported_tasks"] = root_result.get("unsupported_tasks", [])

        # For each template node, create a workflow from the template unit's resolved_doc.
        for node in meta.template_nodes or []:
            stem = re.sub(r"[^a-zA-Z0-9_-]", "_", str(node.get("name") or "template")).lower()
            file_stem = f"{safe_root}__{stem}"

            kind = node.get("kind", "")
            tpl_doc = node.get("resolved_doc") if isinstance(node, dict) else None
            if not isinstance(tpl_doc, dict):
                tpl_doc = {}

            workflow = {
                "name": f"{meta.pipeline_name} / {node.get('name', 'template')}",
                "on": {"workflow_dispatch": {}},
                "jobs": {},
            }

            # Build jobs from template doc based on its kind.
            warnings = results.setdefault("warnings", [])
            unsupported = results.setdefault("unsupported_tasks", [])

            def build_steps(raw_steps: list[Any]) -> list[dict[str, Any]]:
                steps: list[dict[str, Any]] = [{"uses": "actions/checkout@v4"}]
                for rs in raw_steps:
                    mapped = self._map_step(rs, warnings, unsupported) if isinstance(rs, dict) else None
                    if mapped:
                        steps.append(mapped)
                # If bicep is referenced, bootstrap Azure auth + bicep.
                if self._job_uses_bicep(steps):
                    steps = [{"uses": "actions/checkout@v4"}] + self._bicep_bootstrap_steps() + [s for s in steps if s.get("uses") != "actions/checkout@v4"]
                return steps

            if kind == "steps" and isinstance(tpl_doc.get("steps"), list):
                workflow["jobs"]["template"] = {
                    "runs-on": "ubuntu-latest",
                    "steps": build_steps(tpl_doc.get("steps", [])),
                }
            elif kind == "jobs" and isinstance(tpl_doc.get("jobs"), list):
                # Convert each ADO job to a GHA job.
                for idx, raw_job in enumerate(tpl_doc.get("jobs", [])):
                    if not isinstance(raw_job, dict):
                        continue
                    job_body = raw_job.get("job", raw_job)
                    job_name = job_body.get("job", job_body.get("displayName", f"job{idx}"))
                    job_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(job_name)).lower()
                    raw_steps = job_body.get("steps", []) if isinstance(job_body.get("steps"), list) else []
                    workflow["jobs"][job_id] = {
                        "name": str(job_name),
                        "runs-on": "ubuntu-latest",
                        "steps": build_steps(raw_steps),
                    }
            elif kind == "stages" and isinstance(tpl_doc.get("stages"), list):
                # Flatten stages to jobs with needs ignored (template-only view).
                for idx, st in enumerate(tpl_doc.get("stages", [])):
                    if not isinstance(st, dict):
                        continue
                    stage_name = st.get("stage", st.get("displayName", f"stage{idx}"))
                    job_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(stage_name)).lower()
                    steps_accum: list[Any] = []
                    for j in st.get("jobs", []) if isinstance(st.get("jobs"), list) else []:
                        if isinstance(j, dict) and isinstance(j.get("steps"), list):
                            steps_accum.extend(j.get("steps", []))
                    workflow["jobs"][job_id] = {
                        "name": str(stage_name),
                        "runs-on": "ubuntu-latest",
                        "steps": build_steps(steps_accum),
                    }
            else:
                workflow["jobs"]["template"] = {
                    "runs-on": "ubuntu-latest",
                    "steps": [
                        {"uses": "actions/checkout@v4"},
                        {"name": "Template origin", "run": f"echo 'ADO template: {node.get('path', '')}'"},
                        {"name": "TODO", "run": "echo 'Template content could not be mapped automatically'"},
                    ],
                }

            yaml_text = self.render_workflow_yaml(meta=meta, workflow=workflow)
            wf_path = output_dir / f"{file_stem}.yml"
            wf_path.write_text(yaml_text, encoding="utf-8")
            results["workflow_files"].append(str(wf_path))

        return results

    # ── workflow builders ─────────────────────────────────────────────────

    def _build_yaml_workflow(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        workflow: dict[str, Any] = {"name": meta.pipeline_name}
        workflow.update(self._build_triggers(meta, warnings))

        env = self._build_env_block(meta)
        if env:
            workflow["env"] = env

        if meta.stages:
            workflow["jobs"] = self._build_multi_stage_jobs(
                meta, warnings, unsupported,
            )
        elif meta.yaml_content:
            workflow["jobs"] = self._build_jobs_from_yaml(
                meta, warnings, unsupported,
            )
        else:
            workflow["jobs"] = self._build_default_jobs(
                meta, warnings, unsupported,
            )
        return workflow

    def _build_release_workflow(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        warnings.append(
            "Release pipelines require manual review – environment "
            "approval gates have no direct GHA equivalent."
        )
        workflow: dict[str, Any] = {"name": meta.pipeline_name}
        workflow.update(self._build_triggers(meta, warnings))

        env = self._build_env_block(meta)
        if env:
            workflow["env"] = env

        jobs: dict[str, Any] = {}
        for idx, stage in enumerate(meta.stages):
            job_id = re.sub(r"[^a-zA-Z0-9_]", "_", stage.name).lower()
            job: dict[str, Any] = {
                "name": stage.display_name or stage.name,
                "runs-on": self._resolve_runner(stage.agent_pool),
            }
            if stage.depends_on:
                job["needs"] = [
                    re.sub(r"[^a-zA-Z0-9_]", "_", d).lower()
                    for d in stage.depends_on
                ]
            if stage.condition:
                job["if"] = self._map_condition(stage.condition)
            if stage.environment:
                job["environment"] = stage.environment.name
                warnings.append(
                    f"Stage '{stage.name}' uses environment "
                    f"'{stage.environment.name}' – verify GitHub environment "
                    f"protection rules match ADO approvals."
                )
            job["steps"] = [{"uses": "actions/checkout@v4"}]
            for step_dict in stage.jobs:
                for step in step_dict.get("steps", [step_dict]):
                    mapped = self._map_step(step, warnings, unsupported)
                    if mapped:
                        job["steps"].append(mapped)
            jobs[job_id] = job

        if not jobs:
            jobs = self._build_default_jobs(meta, warnings, unsupported)

        workflow["jobs"] = jobs
        return workflow

    def _build_classic_workflow(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        warnings.append(
            "Classic pipelines have no YAML source – the generated workflow "
            "is a best-effort conversion from extracted metadata."
        )
        workflow: dict[str, Any] = {"name": meta.pipeline_name}
        workflow.update(self._build_triggers(meta, warnings))

        env = self._build_env_block(meta)
        if env:
            workflow["env"] = env

        if meta.stages:
            workflow["jobs"] = self._build_multi_stage_jobs(
                meta, warnings, unsupported,
            )
        else:
            workflow["jobs"] = self._build_default_jobs(
                meta, warnings, unsupported,
            )
        return workflow

    # ── triggers ──────────────────────────────────────────────────────────

    def _build_triggers(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
    ) -> dict:
        on: dict[str, Any] = {}

        # Push trigger
        if meta.trigger_branches:
            on["push"] = {"branches": list(meta.trigger_branches)}

        # PR trigger
        if meta.trigger_pr_branches:
            on["pull_request"] = {"branches": list(meta.trigger_pr_branches)}

        # Scheduled triggers
        if meta.trigger_schedules:
            crons: list[dict[str, str]] = []
            for sched in meta.trigger_schedules:
                cron_expr = self._ado_schedule_to_cron(sched, warnings)
                if cron_expr:
                    crons.append({"cron": cron_expr})
            if crons:
                on["schedule"] = crons

        # Always include workflow_dispatch for manual runs.
        on["workflow_dispatch"] = {}

        if not on:
            on["workflow_dispatch"] = {}

        return {"on": on}

    def _ado_schedule_to_cron(
        self,
        sched: dict,
        warnings: list[str],
    ) -> Optional[str]:
        """Convert an ADO schedule dict to a cron expression.

        ADO schedules may carry ``daysToRun`` as a bitmask (Sun=1 …
        Sat=64) or as a list of day names, plus ``hour`` and ``minute``.
        """
        minute = sched.get("minute", sched.get("minutes", 0))
        hour = sched.get("hour", sched.get("hours", 0))

        # Determine day-of-week.
        days_raw = sched.get("daysToRun", sched.get("days_to_build", 0))
        if isinstance(days_raw, int):
            if days_raw == 0:
                dow = "*"
            else:
                dow_parts = []
                for bit, name in zip(_DOW_BITS, _DOW_NAMES):
                    if days_raw & bit:
                        dow_parts.append(name)
                dow = ",".join(dow_parts) if dow_parts else "*"
        elif isinstance(days_raw, list):
            dow = ",".join(str(d).upper()[:3] for d in days_raw) if days_raw else "*"
        else:
            dow = "*"
            warnings.append(
                f"Unrecognised schedule day format: {days_raw!r} – defaulting to '*'."
            )

        branch = sched.get("branch", "")
        if branch:
            warnings.append(
                f"ADO schedule targets branch '{branch}' – GHA schedule "
                f"triggers always run on the default branch."
            )

        return f"{minute} {hour} * * {dow}"

    # ── job builders ──────────────────────────────────────────────────────

    def _build_multi_stage_jobs(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        jobs: dict[str, Any] = {}
        for stage in meta.stages:
            job_id = re.sub(r"[^a-zA-Z0-9_]", "_", stage.name).lower()
            job: dict[str, Any] = {
                "name": stage.display_name or stage.name,
                "runs-on": self._resolve_runner(stage.agent_pool),
            }
            if stage.depends_on:
                job["needs"] = [
                    re.sub(r"[^a-zA-Z0-9_]", "_", d).lower()
                    for d in stage.depends_on
                ]
            if stage.condition:
                job["if"] = self._map_condition(stage.condition)
            if stage.environment and stage.is_deployment:
                job["environment"] = stage.environment.name

            steps: list[dict] = [{"uses": "actions/checkout@v4"}]

            # Stage-level variables as env.
            if stage.variables:
                stage_env = {}
                for v in stage.variables:
                    if v.is_secret:
                        stage_env[v.name] = f"${{{{ secrets.{v.name} }}}}"
                    else:
                        stage_env[v.name] = v.value
                job["env"] = stage_env

            for job_dict in stage.jobs:
                for step in job_dict.get("steps", [job_dict]):
                    mapped = self._map_step(step, warnings, unsupported)
                    if mapped:
                        steps.append(mapped)

            job["steps"] = steps
            jobs[job_id] = job
        return jobs

    def _build_jobs_from_yaml(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        """Attempt to parse steps from embedded YAML content."""
        jobs: dict[str, Any] = {}
        yaml_text = meta.resolved_yaml or meta.yaml_content
        try:
            parsed = yaml.safe_load(yaml_text)
        except Exception:
            warnings.append(
                "Failed to parse embedded YAML content – falling back to "
                "default job scaffold."
            )
            return self._build_default_jobs(meta, warnings, unsupported)

        if not isinstance(parsed, dict):
            return self._build_default_jobs(meta, warnings, unsupported)

        # Handle top-level ``stages`` key.
        raw_stages = parsed.get("stages", [])
        if raw_stages:
            for raw_stage in raw_stages:
                stage_body = raw_stage.get("stage", raw_stage)
                stage_name = stage_body.get("stage", stage_body.get("displayName", "build"))
                job_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(stage_name)).lower()
                raw_jobs = stage_body.get("jobs", [])
                steps: list[dict] = [{"uses": "actions/checkout@v4"}]
                for raw_job in raw_jobs:
                    job_body = raw_job.get("job", raw_job)
                    for step in job_body.get("steps", []):
                        mapped = self._map_step(step, warnings, unsupported)
                        if mapped:
                            steps.append(mapped)
                pool = stage_body.get("pool", {})
                runner = self._resolve_runner(
                    pool.get("vmImage", "ubuntu-latest") if isinstance(pool, dict) else str(pool)
                )
                jobs[job_id] = {
                    "name": str(stage_name),
                    "runs-on": runner,
                    "steps": steps,
                }
            return jobs

        # Handle top-level ``jobs`` key.
        raw_jobs = parsed.get("jobs", [])
        if raw_jobs:
            for idx, raw_job in enumerate(raw_jobs):
                job_body = raw_job.get("job", raw_job)
                job_name = job_body.get("job", job_body.get("displayName", f"job{idx}"))
                job_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(job_name)).lower()
                steps = [{"uses": "actions/checkout@v4"}]
                for step in job_body.get("steps", []):
                    mapped = self._map_step(step, warnings, unsupported)
                    if mapped:
                        steps.append(mapped)
                pool = job_body.get("pool", {})
                runner = self._resolve_runner(
                    pool.get("vmImage", "ubuntu-latest") if isinstance(pool, dict) else str(pool)
                )
                jobs[job_id] = {
                    "name": str(job_name),
                    "runs-on": runner,
                    "steps": steps,
                }
            return jobs

        # Handle top-level ``steps`` only.
        raw_steps = parsed.get("steps", [])
        steps = [{"uses": "actions/checkout@v4"}]
        for step in raw_steps:
            mapped = self._map_step(step, warnings, unsupported)
            if mapped:
                steps.append(mapped)
        pool = parsed.get("pool", {})
        runner = self._resolve_runner(
            pool.get("vmImage", "ubuntu-latest") if isinstance(pool, dict) else str(pool)
        )
        jobs["build"] = {
            "name": "Build",
            "runs-on": runner,
            "steps": steps,
        }
        return jobs

    def _build_default_jobs(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        """Fallback: produce a single job with a TODO placeholder."""
        runner = self._resolve_runner(
            meta.agent_pools[0] if meta.agent_pools else "ubuntu-latest"
        )
        steps: list[dict] = [
            {"uses": "actions/checkout@v4"},
            {
                "name": "TODO: add build steps",
                "run": (
                    'echo "This workflow was auto-generated from ADO pipeline '
                    f"'{meta.pipeline_name}'. Add your build steps here.\""
                ),
            },
        ]
        warnings.append(
            "No stages or steps were found – a placeholder job was generated."
        )
        return {
            "build": {
                "name": "Build",
                "runs-on": runner,
                "steps": steps,
            }
        }

    # ── step mapping ──────────────────────────────────────────────────────

    def _map_step(
        self,
        step: dict,
        warnings: list[str],
        unsupported: list[str],
    ) -> Optional[dict]:
        """Map a single ADO task/step dict to a GitHub Actions step dict."""
        task_name = step.get("task", step.get("taskName", ""))
        display = step.get("displayName", step.get("name", ""))
        inputs = step.get("inputs", {})
        condition = step.get("condition", "")
        env_block = step.get("env", {})
        enabled = step.get("enabled", True)

        if not enabled:
            return None

        gha_step: dict[str, Any] = {}
        if display:
            gha_step["name"] = display

        if condition:
            gha_step["if"] = self._map_condition(condition)

        if env_block:
            gha_step["env"] = dict(env_block)

        # ── Script / inline run steps ────────────────────────────────────
        script = step.get("script", step.get("bash", step.get("powershell", "")))
        if script and not task_name:
            gha_step["run"] = script
            shell = "bash"
            if step.get("powershell"):
                shell = "pwsh"
            gha_step["shell"] = shell
            return gha_step

        if not task_name:
            # Bare ``checkout`` or unknown structure – skip silently.
            if step.get("checkout"):
                return None
            return None

        # ── Lookup in ADO_TASK_MAP ───────────────────────────────────────
        action = ADO_TASK_MAP.get(task_name)

        if action is None:
            unsupported.append(task_name)
            warnings.append(
                f"Task '{task_name}' has no known GHA equivalent – "
                f"added as a commented TODO step."
            )
            gha_step["name"] = f"TODO: migrate '{task_name}'"
            gha_step["run"] = (
                f'echo "ADO task {task_name} needs manual migration"'
            )
            return gha_step

        # ── Run-based tasks ──────────────────────────────────────────────
        if action == "run":
            run_cmd = self._extract_run_command(task_name, inputs)
            gha_step["run"] = run_cmd
            if task_name == "PowerShell@2":
                gha_step["shell"] = "pwsh"
            return gha_step

        # ── Uses-based tasks ─────────────────────────────────────────────
        gha_step["uses"] = action
        with_block = self._extract_with_block(task_name, inputs)
        if with_block:
            gha_step["with"] = with_block

        return gha_step

    @staticmethod
    def _extract_run_command(task_name: str, inputs: dict) -> str:
        """Best-effort extraction of the shell command for run-based tasks."""
        # CmdLine / Bash / PowerShell carry their script in ``script``.
        script = inputs.get("script", "")
        if script:
            return script

        # Npm tasks.
        if task_name.startswith("Npm"):
            command = inputs.get("command", "install")
            working_dir = inputs.get("workingDir", "")
            if working_dir:
                return f"cd {working_dir} && npm {command}"
            return f"npm {command}"

        # NuGet.
        if task_name.startswith("NuGet"):
            command = inputs.get("command", "restore")
            solution = inputs.get("restoreSolution", inputs.get("solution", ""))
            return f"dotnet {command} {solution}".strip()

        # Maven / Gradle.
        if task_name.startswith("Maven"):
            goals = inputs.get("goals", "package")
            pom = inputs.get("mavenPomFile", "pom.xml")
            return f"mvn {goals} -f {pom}"
        if task_name.startswith("Gradle"):
            tasks = inputs.get("tasks", "build")
            return f"./gradlew {tasks}"

        # Terraform.
        if task_name.startswith("Terraform"):
            command = inputs.get("command", "init")
            return f"terraform {command}"

        # Helm.
        if task_name.startswith("Helm"):
            command = inputs.get("command", "install")
            chart = inputs.get("chartPath", inputs.get("chartName", ""))
            return f"helm {command} {chart}".strip()

        # Kubernetes.
        if task_name.startswith("Kubernetes"):
            command = inputs.get("command", "apply")
            arguments = inputs.get("arguments", "")
            return f"kubectl {command} {arguments}".strip()

        # PipAuthenticate / NpmAuthenticate – informational.
        if "Authenticate" in task_name:
            return (
                f'echo "TODO: configure authentication (was {task_name})"'
            )

        return f'echo "TODO: translate ADO task {task_name}"'

    @staticmethod
    def _extract_with_block(task_name: str, inputs: dict) -> dict:
        """Build the ``with:`` block for a uses-based action."""
        w: dict[str, str] = {}

        if task_name == "NodeTool@0":
            version = inputs.get("versionSpec", inputs.get("version", ""))
            if version:
                w["node-version"] = version

        elif task_name == "UsePythonVersion@0":
            version = inputs.get("versionSpec", "")
            if version:
                w["python-version"] = version

        elif task_name == "DotNetCoreCLI@2":
            version = inputs.get("version", inputs.get("packagesToPush", ""))
            if version:
                w["dotnet-version"] = version

        elif task_name == "JavaToolInstaller@0":
            version = inputs.get("versionSpec", "")
            vendor = inputs.get("jdkArchitectureOption", "temurin")
            if version:
                w["java-version"] = version
            w["distribution"] = vendor

        elif task_name == "GoTool@0":
            version = inputs.get("version", "")
            if version:
                w["go-version"] = version

        elif task_name == "Docker@2":
            context = inputs.get("buildContext", ".")
            dockerfile = inputs.get("Dockerfile", inputs.get("dockerfile", ""))
            push_flag = inputs.get("push", "false")
            tags = inputs.get("tags", "")
            w["context"] = context
            if dockerfile:
                w["file"] = dockerfile
            w["push"] = push_flag
            if tags:
                w["tags"] = tags

        elif task_name == "AzureCLI@2":
            script = inputs.get("inlineScript", inputs.get("scriptType", ""))
            if script:
                w["inlineScript"] = script

        elif task_name == "AzureWebApp@1":
            app_name = inputs.get("appName", "")
            package = inputs.get("package", "")
            if app_name:
                w["app-name"] = app_name
            if package:
                w["package"] = package

        elif task_name == "AzureFunctionApp@2":
            app_name = inputs.get("appName", "")
            package = inputs.get("package", "")
            if app_name:
                w["app-name"] = app_name
            if package:
                w["package"] = package

        elif task_name == "PublishBuildArtifacts@1":
            path = inputs.get("PathtoPublish", inputs.get("pathToPublish", "."))
            artifact = inputs.get("ArtifactName", inputs.get("artifactName", "drop"))
            w["name"] = artifact
            w["path"] = path

        elif task_name == "DownloadBuildArtifacts@0":
            artifact = inputs.get("artifactName", "drop")
            w["name"] = artifact

        elif task_name == "PublishTestResults@2":
            fmt = inputs.get("testResultsFormat", "JUnit")
            files = inputs.get("testResultsFiles", "")
            w["reporter"] = fmt.lower()
            if files:
                w["path"] = files

        return w

    # ── condition mapping ─────────────────────────────────────────────────

    @staticmethod
    def _map_condition(condition: str) -> str:
        """Translate common ADO pipeline conditions to GHA ``if:`` expressions."""
        if not condition:
            return ""

        c = condition.strip()

        # succeeded() / failed() / always() / canceled()
        c = re.sub(r"\bsucceeded\(\)", "success()", c)
        c = re.sub(r"\bfailed\(\)", "failure()", c)
        c = re.sub(r"\balways\(\)", "always()", c)
        c = re.sub(r"\bcanceled\(\)", "cancelled()", c)

        # Variable references: variables['foo'] or variables.foo
        c = re.sub(
            r"variables\[(['\"])(.+?)\1]",
            r"env.\2",
            c,
        )
        c = re.sub(r"variables\.(\w+)", r"env.\1", c)

        # eq / ne / and / or / not
        c = re.sub(r"\beq\(", "== (", c)
        c = re.sub(r"\bne\(", "!= (", c)
        c = re.sub(r"\band\(", "&& (", c)
        c = re.sub(r"\bor\(", "|| (", c)
        c = re.sub(r"\bnot\(", "! (", c)

        # Build.SourceBranch
        c = re.sub(r"Build\.SourceBranch", "github.ref", c)
        c = re.sub(r"Build\.Reason", "github.event_name", c)

        return c

    # ── runner resolution ─────────────────────────────────────────────────

    @staticmethod
    def _resolve_runner(pool_name: str) -> str:
        """Map an ADO agent pool name to the closest GitHub-hosted runner."""
        if not pool_name:
            return "ubuntu-latest"
        lower = pool_name.lower()
        for fragment, runner in _POOL_RUNNER_MAP.items():
            if fragment in lower:
                return runner
        return "ubuntu-latest"

    # ── environment block helper ──────────────────────────────────────────

    @staticmethod
    def _build_env_block(meta: PipelineMetadata) -> dict[str, str]:
        env: dict[str, str] = {}
        for v in meta.variables:
            if v.is_secret:
                env[v.name] = f"${{{{ secrets.{v.name} }}}}"
            else:
                env[v.name] = v.value
        return env

    # ── migration notes ───────────────────────────────────────────────────

    @staticmethod
    def _write_migration_notes(
        meta: PipelineMetadata,
        output_dir: Path,
        safe_name: str,
        warnings: list[str],
        unsupported: list[str],
    ) -> Path:
        """Generate a markdown migration-notes file alongside the workflow."""
        notes_file = output_dir / f"{safe_name}_migration_notes.md"
        lines: list[str] = [
            f"# Migration Notes – {meta.pipeline_name}",
            "",
            "## Pipeline Information",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Pipeline ID | {meta.pipeline_id} |",
            f"| Pipeline Name | {meta.pipeline_name} |",
            f"| Type | {meta.pipeline_type.value} |",
            f"| Project | {meta.project} |",
            f"| Repository | {meta.repo_name} |",
            f"| Complexity | {meta.complexity.value} |",
            f"| Avg Duration | {meta.avg_duration_min:.1f} min |",
            f"| Runs (last 30 days) | {meta.total_runs_30d} |",
            "",
        ]

        if meta.service_connections:
            lines.append("## Service Connections")
            lines.append("")
            lines.append(
                "The following ADO service connections must be replaced with "
                "GitHub secrets or OIDC federation:"
            )
            lines.append("")
            for sc in meta.service_connections:
                sc_name = sc.get("name", sc) if isinstance(sc, dict) else str(sc)
                lines.append(f"- `{sc_name}`")
            lines.append("")

        if meta.variable_groups:
            lines.append("## Variable Groups")
            lines.append("")
            lines.append(
                "ADO variable groups must be migrated to GitHub Actions "
                "secrets / variables or environment-level settings:"
            )
            lines.append("")
            for vg in meta.variable_groups:
                vg_name = vg.get("name", vg) if isinstance(vg, dict) else str(vg)
                lines.append(f"- `{vg_name}`")
            lines.append("")

        if meta.environments:
            lines.append("## Environments")
            lines.append("")
            lines.append(
                "Create matching GitHub environments with appropriate "
                "protection rules:"
            )
            lines.append("")
            for env in meta.environments:
                approvers = ", ".join(env.required_approvers) or "none"
                lines.append(
                    f"- **{env.name}** – approvers: {approvers}, "
                    f"timeout: {env.approval_timeout_min} min"
                )
            lines.append("")

        if unsupported:
            lines.append("## Unsupported Tasks")
            lines.append("")
            lines.append(
                "The following ADO tasks have no direct GitHub Actions "
                "equivalent and require manual migration:"
            )
            lines.append("")
            for task in sorted(set(unsupported)):
                lines.append(f"- `{task}`")
            lines.append("")

        if warnings:
            lines.append("## Warnings")
            lines.append("")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

        if meta.migration_notes:
            lines.append("## Additional Notes")
            lines.append("")
            for note in meta.migration_notes:
                lines.append(f"- {note}")
            lines.append("")

        notes_file.write_text("\n".join(lines), encoding="utf-8")
        return notes_file

    def _bicep_bootstrap_steps(self) -> list[dict[str, Any]]:
        """Bootstrap steps needed for most Bicep deployments."""
        return [
            {
                "name": "Azure login (OIDC recommended)",
                "uses": "azure/login@v2",
                "with": {
                    "client-id": "${{ secrets.AZURE_CLIENT_ID }}",
                    "tenant-id": "${{ secrets.AZURE_TENANT_ID }}",
                    "subscription-id": "${{ secrets.AZURE_SUBSCRIPTION_ID }}",
                },
            },
            {
                "name": "Ensure Bicep available",
                "shell": "bash",
                "run": """set -euo pipefail
if az bicep install --upgrade; then
  echo "Bicep installed"
else
  az bicep version
fi
""",
            },
        ]

    @staticmethod
    def _job_uses_bicep(steps: list[dict[str, Any]]) -> bool:
        for s in steps:
            if not isinstance(s, dict):
                continue
            run = s.get("run")
            if isinstance(run, str) and (".bicep" in run.lower() or " bicep " in f" {run.lower()} "):
                return True
        return False
