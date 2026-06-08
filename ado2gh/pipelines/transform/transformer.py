"""Orchestrator — converts ADO PipelineMetadata to GitHub Actions YAML."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ado2gh.models import PipelineMetadata, PipelineType
from ado2gh.pipelines.transform.expressions import (
    map_condition,
    rewrite_expressions_inplace,
)
from ado2gh.pipelines.transform.job_graph import (
    build_default_jobs,
    build_jobs_from_yaml,
    build_multi_stage_jobs,
    resolve_runner,
)
from ado2gh.pipelines.transform.task_registry import (
    lookup_task,
)
from ado2gh.pipelines.transform.triggers import build_triggers

logger = logging.getLogger(__name__)


class PipelineTransformer:
    """Convert an ADO ``PipelineMetadata`` object into a GitHub Actions workflow."""

    def transform(self, meta: PipelineMetadata, output_dir: Path) -> dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        unsupported: list[str] = list(meta.unsupported_tasks)

        if meta.pipeline_type == PipelineType.YAML:
            workflow = self._build_yaml_workflow(meta, warnings, unsupported)
        elif meta.pipeline_type == PipelineType.RELEASE:
            workflow = self._build_release_workflow(meta, warnings, unsupported)
        else:
            workflow = self._build_classic_workflow(meta, warnings, unsupported)

        env_keys = set(workflow.get("env", {}).keys())
        rewrite_expressions_inplace(workflow, env_keys)

        header = (
            f"# ---------------------------------------------------------\n"
            f"# Auto-generated GitHub Actions workflow\n"
            f"# Source: ADO pipeline '{meta.pipeline_name}' "
            f"(id={meta.pipeline_id}, type={meta.pipeline_type.value})\n"
            f"# Project: {meta.project}  Repo: {meta.repo_name}\n"
            f"# Generated: {datetime.now(timezone.utc).isoformat()}\n"
            f"# ---------------------------------------------------------\n\n"
        )
        yaml_text = header + yaml.dump(
            workflow, default_flow_style=False, sort_keys=False,
            allow_unicode=True, width=120,
        )

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", meta.pipeline_name).lower()
        workflow_file = output_dir / f"{safe_name}.yml"
        workflow_file.write_text(yaml_text, encoding="utf-8")

        notes_file = self._write_migration_notes(
            meta, output_dir, safe_name, warnings, unsupported,
        )

        logger.info(
            "Transformed pipeline %s (%s) -> %s  (%d warnings, %d unsupported)",
            meta.pipeline_name, meta.pipeline_type.value, workflow_file,
            len(warnings), len(unsupported),
        )

        return {
            "workflow_file": workflow_file,
            "notes_file": notes_file,
            "warnings": warnings,
            "unsupported_tasks": unsupported,
        }

    def _build_yaml_workflow(
        self, meta: PipelineMetadata, warnings: list[str], unsupported: list[str],
    ) -> dict:
        workflow: dict[str, Any] = {"name": meta.pipeline_name}
        workflow.update(build_triggers(meta, warnings))
        env = self._build_env_block(meta)
        if env:
            workflow["env"] = env

        if meta.stages:
            workflow["jobs"] = build_multi_stage_jobs(
                meta, warnings, unsupported, self._map_step,
            )
        elif meta.yaml_content:
            workflow["jobs"] = build_jobs_from_yaml(
                meta, warnings, unsupported, self._map_step,
                lambda: build_default_jobs(meta, warnings),
            )
        else:
            workflow["jobs"] = build_default_jobs(meta, warnings)
        return workflow

    def _build_release_workflow(
        self, meta: PipelineMetadata, warnings: list[str], unsupported: list[str],
    ) -> dict:
        warnings.append(
            "Release pipelines require manual review – environment "
            "approval gates have no direct GHA equivalent."
        )
        workflow: dict[str, Any] = {"name": meta.pipeline_name}
        workflow.update(build_triggers(meta, warnings))
        env = self._build_env_block(meta)
        if env:
            workflow["env"] = env

        jobs: dict[str, Any] = {}
        for stage in meta.stages:
            job_id = re.sub(r"[^a-zA-Z0-9_]", "_", stage.name).lower()
            job: dict[str, Any] = {
                "name": stage.display_name or stage.name,
                "runs-on": resolve_runner(stage.agent_pool),
            }
            if stage.depends_on:
                job["needs"] = [
                    re.sub(r"[^a-zA-Z0-9_]", "_", d).lower()
                    for d in stage.depends_on
                ]
            if stage.condition:
                job["if"] = map_condition(stage.condition)
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
            jobs = build_default_jobs(meta, warnings)
        workflow["jobs"] = jobs
        return workflow

    def _build_classic_workflow(
        self, meta: PipelineMetadata, warnings: list[str], unsupported: list[str],
    ) -> dict:
        warnings.append(
            "Classic pipelines have no YAML source – the generated workflow "
            "is a best-effort conversion from extracted metadata."
        )
        workflow: dict[str, Any] = {"name": meta.pipeline_name}
        workflow.update(build_triggers(meta, warnings))
        env = self._build_env_block(meta)
        if env:
            workflow["env"] = env

        if meta.stages:
            workflow["jobs"] = build_multi_stage_jobs(
                meta, warnings, unsupported, self._map_step,
            )
        else:
            workflow["jobs"] = build_default_jobs(meta, warnings)
        return workflow

    def _map_step(
        self, step: dict, warnings: list[str], unsupported: list[str],
    ) -> Optional[dict]:
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
            gha_step["if"] = map_condition(condition)
        if env_block:
            gha_step["env"] = dict(env_block)

        script = step.get("script", step.get("bash", step.get("powershell", "")))
        if script and not task_name:
            gha_step["run"] = script
            gha_step["shell"] = "pwsh" if step.get("powershell") else "bash"
            return gha_step

        if not task_name:
            if step.get("checkout"):
                return None
            return None

        action = lookup_task(task_name)
        if action is None:
            unsupported.append(task_name)
            warnings.append(
                f"Task '{task_name}' has no known GHA equivalent – "
                f"added as a commented TODO step."
            )
            gha_step["name"] = f"TODO: migrate '{task_name}'"
            gha_step["run"] = f'echo "ADO task {task_name} needs manual migration"'
            return gha_step

        if action == "run":
            gha_step["run"] = self._extract_run_command(task_name, inputs)
            if task_name == "PowerShell@2":
                gha_step["shell"] = "pwsh"
            return gha_step

        gha_step["uses"] = action
        with_block = self._extract_with_block(task_name, inputs)
        if with_block:
            gha_step["with"] = with_block
        return gha_step

    @staticmethod
    def _extract_run_command(task_name: str, inputs: dict) -> str:
        script = inputs.get("script", "")
        if script:
            return script
        if task_name.startswith("Npm"):
            command = inputs.get("command", "install")
            working_dir = inputs.get("workingDir", "")
            if working_dir:
                return f"cd {working_dir} && npm {command}"
            return f"npm {command}"
        if task_name.startswith("NuGet"):
            command = inputs.get("command", "restore")
            solution = inputs.get("restoreSolution", inputs.get("solution", ""))
            return f"dotnet {command} {solution}".strip()
        if task_name.startswith("Maven"):
            goals = inputs.get("goals", "package")
            pom = inputs.get("mavenPomFile", "pom.xml")
            return f"mvn {goals} -f {pom}"
        if task_name.startswith("Gradle"):
            tasks = inputs.get("tasks", "build")
            return f"./gradlew {tasks}"
        if task_name.startswith("Terraform"):
            return f"terraform {inputs.get('command', 'init')}"
        if task_name.startswith("Helm"):
            command = inputs.get("command", "install")
            chart = inputs.get("chartPath", inputs.get("chartName", ""))
            return f"helm {command} {chart}".strip()
        if task_name.startswith("Kubernetes"):
            command = inputs.get("command", "apply")
            arguments = inputs.get("arguments", "")
            return f"kubectl {command} {arguments}".strip()
        if "Authenticate" in task_name:
            return f'echo "TODO: configure authentication (was {task_name})"'
        return f'echo "TODO: translate ADO task {task_name}"'

    @staticmethod
    def _extract_with_block(task_name: str, inputs: dict) -> dict:
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
            w["context"] = inputs.get("buildContext", ".")
            dockerfile = inputs.get("Dockerfile", inputs.get("dockerfile", ""))
            if dockerfile:
                w["file"] = dockerfile
            w["push"] = inputs.get("push", "false")
            tags = inputs.get("tags", "")
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
            w["name"] = inputs.get("artifactName", "drop")
        elif task_name == "PublishTestResults@2":
            fmt = inputs.get("testResultsFormat", "JUnit")
            files = inputs.get("testResultsFiles", "")
            w["reporter"] = fmt.lower()
            if files:
                w["path"] = files
        return w

    @staticmethod
    def _build_env_block(meta: PipelineMetadata) -> dict[str, str]:
        env: dict[str, str] = {}
        for v in meta.variables:
            if v.is_secret:
                env[v.name] = f"${{{{ secrets.{v.name} }}}}"
            else:
                env[v.name] = v.value
        return env

    @staticmethod
    def _write_migration_notes(
        meta: PipelineMetadata, output_dir: Path, safe_name: str,
        warnings: list[str], unsupported: list[str],
    ) -> Path:
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
            lines += ["## Service Connections", ""]
            for sc in meta.service_connections:
                sc_name = sc.get("name", sc) if isinstance(sc, dict) else str(sc)
                lines.append(f"- `{sc_name}`")
            lines.append("")
        if unsupported:
            lines += ["## Unsupported Tasks", ""]
            for task in sorted(set(unsupported)):
                lines.append(f"- `{task}`")
            lines.append("")
        if warnings:
            lines += ["## Warnings", ""]
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")
        notes_file.write_text("\n".join(lines), encoding="utf-8")
        return notes_file
