"""Transforms ADO PipelineMetadata into GitHub Actions workflow YAML."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import yaml

from ado2gh.models import (
    PipelineMetadata,
    PipelineType,
    PipelineComplexity,
    PipelineStage,
    PipelineVariable,
    PipelineEnvironment,
)

logger = logging.getLogger(__name__)

# ── ADO task -> GitHub Actions action mapping ────────────────────────────────

ADO_TASK_MAP: dict[str, str] = {
    # Setup / tool-installer tasks
    "NodeTool@0":              "actions/setup-node@v4",
    "UsePythonVersion@0":      "actions/setup-python@v5",
    "UseDotNet@2":             "actions/setup-dotnet@v4",
    # DotNetCoreCLI executes restore/build/test/publish; it is not a tool
    # installer and therefore maps to a dotnet command rather than setup-dotnet.
    "DotNetCoreCLI@2":         "run",
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
    "PublishPipelineArtifact@1": "actions/upload-artifact@v4",
    "DownloadPipelineArtifact@2": "actions/download-artifact@v4",
    "Cache@2":                  "actions/cache@v4",
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

# Immutable commits behind the major tags used above, resolved from the
# publishers' official Git repositories on 2026-07-21. Updating one is an
# explicit dependency-review event rather than an implicit moving-tag update.
ENTERPRISE_ACTION_PINS: dict[str, str] = {
    "actions/checkout@v4": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node@v4": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/setup-python@v5": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-dotnet@v4": "67a3573c9a986a3f9c594539f4ab511d57bb3ce9",
    "actions/setup-java@v4": "c1e323688fd81a25caa38c78aa6df2d33d3e20d9",
    "actions/setup-go@v5": "40f1582b2485089dde7abd97c1529aa768e1baff",
    "actions/upload-artifact@v4": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact@v4": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/cache@v4": "0057852bfaa89a56745cba8c7296529d2fc39830",
    "docker/build-push-action@v5": "ca052bb54ab0790a636c9b5f226502c73d547a25",
    "azure/CLI@v2": "d4515bc8e518874d3c814bf5c307a1cb08ec9b35",
    "azure/webapps-deploy@v3": "b686016b4de8820a23519503685d8d03fbfa03a6",
    "azure/functions-action@v1": "c9cc8ee93d1285e2a17312cc8357dc1775955272",
    "dorny/test-reporter@v1": "3eeb9fc888e82e8be2fb356bbeec2750231672bc",
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
    """Convert an ADO ``PipelineMetadata`` object into a GitHub Actions
    workflow YAML file together with a companion migration-notes document."""

    def __init__(
        self,
        *,
        llm_client: Any = None,
        planner: Any = None,
        validator: Any = None,
        min_llm_confidence: float = 0.75,
        max_llm_resolutions: int = 32,
        require_llm_for_ambiguity: bool = False,
        require_production_ready: bool = False,
        manual_approval_manifest: Any = None,
        external_approval: Optional[Callable[[Any, PipelineMetadata], Any]] = None,
        credential_attestation_verifier: Any = None,
        include_pipeline_identity_in_filename: bool = False,
        action_pins: Optional[Mapping[str, str]] = None,
        review_staging_branch: str = "ado2gh/migrated-workflows",
    ) -> None:
        """Configure the PEV facade while preserving the historical API.

        No provider is created implicitly here: ``PipelineTransformer()`` is
        safe for offline/deterministic use. Enterprise callers should use
        ``create_enterprise_pipeline_transformer_from_env``.
        """
        self._pev_options = {
            "planner": planner,
            "validator": validator,
            "llm_client": llm_client,
            "min_llm_confidence": min_llm_confidence,
            "max_llm_resolutions": max_llm_resolutions,
            "require_llm_for_ambiguity": require_llm_for_ambiguity,
            "require_production_ready": require_production_ready,
            "manual_approval_manifest": manual_approval_manifest,
            "external_approval": external_approval,
            "credential_attestation_verifier": credential_attestation_verifier,
        }
        self._include_pipeline_identity_in_filename = include_pipeline_identity_in_filename
        self._action_pins = dict(action_pins or {})
        self._review_staging_branch = self._normalize_review_staging_branch(
            review_staging_branch
        )

    @staticmethod
    def _defensive_checkout_step() -> dict[str, Any]:
        """Return a checkout that never persists the repository token."""
        return {
            "uses": "actions/checkout@v4",
            "with": {"persist-credentials": False},
        }

    # ── public API ────────────────────────────────────────────────────────

    def transform(
        self,
        meta: PipelineMetadata,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Run Planner -> bounded execution -> deterministic validation.

        Existing callers receive the historical file/warning keys plus plan,
        validation, readiness, evidence, and audit statistics.
        """
        from ado2gh.pipelines.pev import PipelinePEVConverter

        return PipelinePEVConverter(self, **self._pev_options).convert(meta, output_dir)

    def _transform_deterministic(
        self,
        meta: PipelineMetadata,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Execute only the deterministic rules selected by the planner.

        Returns a dict with keys:
            workflow_file  – Path to the generated ``.yml`` file.
            notes_file     – Path to the markdown migration-notes file.
            warnings       – list[str] of conversion warnings.
            unsupported_tasks – list[str] of ADO tasks without a direct mapping.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        unsupported: list[str] = list(meta.unsupported_tasks)

        # Build the workflow dict depending on pipeline type.
        if meta.pipeline_type == PipelineType.YAML:
            workflow = self._build_yaml_workflow(meta, warnings, unsupported)
        elif meta.pipeline_type == PipelineType.RELEASE:
            workflow = self._build_release_workflow(meta, warnings, unsupported)
        else:
            workflow = self._build_classic_workflow(meta, warnings, unsupported)

        # ADO syntax -> GitHub Actions syntax in every string value:
        #   ${{ parameters.X }}  ->  ${{ inputs.X }}
        #   $(X)                 ->  ${{ env.X }}
        # Without this the generated workflow fails GitHub's validator with
        # "Unrecognized named-value: 'parameters'" or doesn't expand $() macros.
        env_keys = {
            self._ado_env_name(variable.name)
            for variable in meta.variables
            if not variable.is_secret
        }
        env_keys.update(
            self._ado_env_name(variable.name)
            for stage in meta.stages
            for variable in stage.variables
            if not variable.is_secret
        )
        self._rewrite_ado_expressions_inplace(workflow, env_keys)
        # A workflow committed to the review branch is still live GitHub
        # Actions configuration.  Trigger filters alone are insufficient:
        # pull_request and workflow_dispatch can both select the staged file.
        # Keep an exact, permanent job-level fence in every generated job so
        # no unreviewed artifact can execute from, or for a PR whose head is,
        # the staging branch.
        self._apply_review_staging_job_guards(workflow)
        self._pin_action_references(workflow)

        # Serialise to YAML with a header comment.
        header = (
            f"# ---------------------------------------------------------\n"
            f"# Auto-generated GitHub Actions workflow\n"
            f"# Source: ADO pipeline '{meta.pipeline_name}' "
            f"(id={meta.pipeline_id}, type={meta.pipeline_type.value})\n"
            f"# Project: {meta.project}  Repo: {meta.repo_name}\n"
            f"# ---------------------------------------------------------\n\n"
        )
        yaml_text = header + yaml.dump(
            workflow,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", meta.pipeline_name).lower().strip("_")
        if not safe_name:
            safe_name = f"pipeline_{meta.pipeline_id}"
        if self._include_pipeline_identity_in_filename:
            safe_name = f"{safe_name}_{meta.pipeline_type.value}_{meta.pipeline_id}"
        workflow_file = output_dir / f"{safe_name}.yml"
        workflow_file.write_text(yaml_text, encoding="utf-8")

        notes_file = self._write_migration_notes(
            meta, output_dir, safe_name, warnings, unsupported,
        )

        logger.info(
            "Transformed pipeline %s (%s) -> %s  (%d warnings, %d unsupported)",
            meta.pipeline_name,
            meta.pipeline_type.value,
            workflow_file,
            len(warnings),
            len(unsupported),
        )

        return {
            "workflow_file": workflow_file,
            "notes_file": notes_file,
            "warnings": warnings,
            "unsupported_tasks": unsupported,
        }

    # ── workflow builders ─────────────────────────────────────────────────

    def _build_yaml_workflow(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        workflow: dict[str, Any] = {
            "name": meta.pipeline_name,
            "permissions": {"contents": "read"},
        }
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

    def _pin_action_references(self, value: Any) -> None:
        """Replace reviewed action tags with immutable commit identifiers."""
        if isinstance(value, dict):
            uses = value.get("uses")
            if isinstance(uses, str) and "@" in uses \
                    and not uses.startswith(("./", "docker://")):
                action_path, ref = uses.rsplit("@", 1)
                parts = action_path.split("/")
                repository = "/".join(parts[:2]) if len(parts) >= 2 else action_path
                pin = (
                    self._action_pins.get(uses)
                    or self._action_pins.get(f"{repository}@{ref}")
                    or self._action_pins.get(repository)
                )
                if pin:
                    value["uses"] = f"{action_path}@{pin}"
            for child in value.values():
                self._pin_action_references(child)
        elif isinstance(value, list):
            for child in value:
                self._pin_action_references(child)

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
        workflow: dict[str, Any] = {
            "name": meta.pipeline_name,
            "permissions": {"contents": "read"},
        }
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
            job["steps"] = [self._defensive_checkout_step()]
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
        workflow: dict[str, Any] = {
            "name": meta.pipeline_name,
            "permissions": {"contents": "read"},
        }
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

        if meta.trigger_batch:
            warnings.append(
                "ADO CI batch=true has no exact GitHub trigger equivalent; "
                "no concurrency policy was emitted."
            )
        for label, values in (
            ("CI branch exclusions", meta.trigger_branch_excludes),
            ("CI path inclusions", meta.trigger_path_includes),
            ("CI path exclusions", meta.trigger_path_excludes),
            ("PR branch exclusions", meta.trigger_pr_branch_excludes),
            ("PR path inclusions", meta.trigger_pr_path_includes),
            ("PR path exclusions", meta.trigger_pr_path_excludes),
        ):
            if values:
                warnings.append(
                    f"ADO {label} {values!r} were retained for review but "
                    "not emitted because filter-grammar parity is unproven."
                )
        if meta.trigger_pr_auto_cancel is True:
            warnings.append(
                "ADO PR autoCancel=true was retained for review; no inexact "
                "GitHub concurrency policy was emitted."
            )
        if meta.trigger_pr_drafts is False:
            warnings.append(
                "ADO PR drafts=false was retained for review; the generated "
                "pull_request event does not suppress draft PR runs."
            )

        # Push trigger
        if meta.trigger_branches:
            from ado2gh.pipelines.planner import PipelineConversionPlanner
            branches = [
                branch for branch in dict.fromkeys(meta.trigger_branches)
                if PipelineConversionPlanner._trigger_branch_literal_is_exact(
                    str(branch)
                )
            ]
            if len(branches) != len(list(dict.fromkeys(meta.trigger_branches))):
                warnings.append(
                    "Unsafe or grammar-ambiguous ADO branch/tag filters were "
                    "not emitted; the PEV plan remains blocked."
                )
            # The review branch contains an unmerged workflow artifact. It
            # must never execute merely because publishing it is itself a
            # push. GitHub evaluates branch patterns in order, so the exact
            # negative pattern is deliberately last and easy for the remote
            # publisher/validator to prove.
            branches = [
                branch for branch in branches
                if branch != self.review_staging_branch_exclusion
            ]
            if branches:
                branches.append(self.review_staging_branch_exclusion)
                on["push"] = {"branches": branches}

        # PR trigger
        if (
            meta.trigger_pr_branches
            and str(meta.repo_type or "").casefold() != "tfsgit"
        ):
            from ado2gh.pipelines.planner import PipelineConversionPlanner
            pr_branches = [
                branch for branch in dict.fromkeys(meta.trigger_pr_branches)
                if PipelineConversionPlanner._trigger_branch_literal_is_exact(
                    str(branch)
                )
            ]
            if len(pr_branches) != len(list(dict.fromkeys(meta.trigger_pr_branches))):
                warnings.append(
                    "Unsafe or grammar-ambiguous ADO PR branch filters were not emitted."
                )
            if pr_branches:
                on["pull_request"] = {"branches": pr_branches}
        elif meta.trigger_pr_branches:
            warnings.append(
                "Azure Repos Git YAML pr syntax is inactive in ADO and was not "
                "converted into an active GitHub pull_request trigger."
            )

        # Scheduled triggers
        if meta.trigger_schedules:
            crons: list[dict[str, str]] = []
            for sched in meta.trigger_schedules:
                cron_expr = self._ado_schedule_to_cron(sched, warnings)
                if cron_expr:
                    crons.append({"cron": cron_expr})
            if crons:
                on["schedule"] = crons

        # Always include workflow_dispatch for manual runs. If the source
        # pipeline declared template `parameters:`, expose them as
        # workflow_dispatch.inputs so `${{ inputs.X }}` references resolve.
        wd: dict = {}
        if getattr(meta, "parameters", None):
            inputs: dict = {}
            for p in meta.parameters:
                name = p.get("name")
                if not name:
                    continue
                p_type = (p.get("type") or "string").lower()
                values = p.get("values")
                gha_input: dict = {"required": p.get("default") is None}
                if isinstance(values, list) and values:
                    gha_input["type"] = "choice"
                    gha_input["options"] = [str(v) for v in values]
                elif p_type in ("boolean", "bool"):
                    gha_input["type"] = "boolean"
                elif p_type == "number":
                    gha_input["type"] = "number"
                else:
                    gha_input["type"] = "string"
                if p.get("default") is not None:
                    gha_input["default"] = (
                        str(p["default"]) if gha_input["type"] != "boolean"
                        else bool(p["default"])
                    )
                inputs[name] = gha_input
            if inputs:
                wd["inputs"] = inputs
        on["workflow_dispatch"] = wd

        if not on:
            on["workflow_dispatch"] = wd

        return {"on": on}

    @property
    def review_staging_branch_exclusion(self) -> str:
        return "!" + self._review_staging_branch

    @property
    def review_staging_job_guard(self) -> str:
        """Return the canonical review-branch job execution fence."""
        return (
            f"github.ref != 'refs/heads/{self._review_staging_branch}' && "
            f"github.head_ref != '{self._review_staging_branch}'"
        )

    def _apply_review_staging_job_guards(
        self, workflow: dict[str, Any]
    ) -> None:
        """Conjoin the canonical staging fence with every generated job.

        The stable textual form is deliberate.  The remote publisher parses
        the exact validator-approved bytes and independently proves this
        prefix before it creates a review ref.
        """
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            raise ValueError("Generated workflow must contain at least one job")
        guard = self.review_staging_job_guard
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                raise ValueError(f"Generated workflow job {job_id!r} is malformed")
            existing = job.get("if")
            if existing is None or not str(existing).strip():
                job["if"] = guard
                continue
            expression = str(existing).strip()
            if expression.startswith("${{") and expression.endswith("}}"):
                expression = expression[3:-2].strip()
            job["if"] = f"{guard} && ({expression})"

    @staticmethod
    def _normalize_review_staging_branch(value: str) -> str:
        branch = str(value or "").strip()
        if branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/"):]
        components = branch.split("/")
        if (
            not branch
            or len(branch) > 255
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch)
            or any(
                not component
                or component.startswith(".")
                or component.endswith((".", ".lock"))
                for component in components
            )
            or "//" in branch
            or ".." in branch
        ):
            raise ValueError(
                "review_staging_branch must be a literal safe Git branch name"
            )
        return branch

    def _ado_schedule_to_cron(
        self,
        sched: dict,
        warnings: list[str],
    ) -> Optional[str]:
        """Convert an ADO schedule dict to a cron expression.

        ADO schedules may carry ``daysToRun`` as a bitmask (Sun=1 …
        Sat=64) or as a list of day names, plus ``hour`` and ``minute``.
        """
        branch = sched.get("branch", "")
        branch_filters = sched.get("branch_filters", [])
        excluded_branches = sched.get("excluded_branches", [])
        if branch or branch_filters:
            warnings.append(
                "ADO schedule branch selection "
                f"{branch or branch_filters!r} cannot be encoded in a GitHub "
                "schedule event; GitHub runs the workflow from the default branch."
            )
        if excluded_branches:
            warnings.append(
                "ADO schedule branch exclusions "
                f"{excluded_branches!r} cannot be encoded in a GitHub schedule event."
            )
        if sched.get("always") is not True:
            warnings.append(
                "ADO schedule changes-only semantics (`always: false`) have no "
                "native GitHub schedule equivalent; operator parity review is required."
            )

        direct_cron = sched.get("cron")
        if isinstance(direct_cron, str) and direct_cron.strip():
            cron = " ".join(direct_cron.split())
            if len(cron.split()) != 5:
                warnings.append(f"Invalid five-field ADO cron expression {direct_cron!r}; schedule skipped.")
                return None
            return cron

        minute = sched.get("minute", sched.get("minutes", sched.get("start_minutes", 0)))
        hour = sched.get("hour", sched.get("hours", sched.get("start_hours", 0)))

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

        return f"{minute} {hour} * * {dow}"

    # ── job builders ──────────────────────────────────────────────────────

    def _build_multi_stage_jobs(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        jobs: dict[str, Any] = {}
        stage_ids: dict[int, list[str]] = {}
        stage_lookup: dict[str, list[str]] = {}
        job_name_ids: dict[tuple[int, str], str] = {}
        allocated: set[str] = set()

        # First allocate stable unique ids so stage/job dependencies can be
        # resolved before emitting any job. Previous behavior collapsed every
        # ADO job in a stage into one GHA job, losing pools and dependencies.
        for stage_index, stage in enumerate(meta.stages):
            raw_jobs = [j for j in stage.jobs if isinstance(j, dict)]
            if not raw_jobs:
                raw_jobs = [{}]
            ids: list[str] = []
            for job_index, raw_job in enumerate(raw_jobs):
                raw_name = str(
                    raw_job.get("job", raw_job.get("deployment", ""))
                    or (stage.name if len(raw_jobs) == 1 else f"job_{job_index + 1}")
                )
                if len(raw_jobs) == 1:
                    candidate = self._normalise_job_id(stage.name)
                else:
                    candidate = self._normalise_job_id(f"{stage.name}_{raw_name}")
                job_id = candidate
                suffix = 2
                while job_id in allocated:
                    job_id = f"{candidate}_{suffix}"
                    suffix += 1
                allocated.add(job_id)
                ids.append(job_id)
                job_name_ids[(stage_index, raw_name)] = job_id
                job_name_ids[(stage_index, self._normalise_job_id(raw_name))] = job_id
            stage_ids[stage_index] = ids
            stage_lookup[stage.name] = ids
            stage_lookup[self._normalise_job_id(stage.name)] = ids

        for stage_index, stage in enumerate(meta.stages):
            raw_jobs = [j for j in stage.jobs if isinstance(j, dict)]
            if not raw_jobs:
                raw_jobs = [{}]
            for job_index, raw_job in enumerate(raw_jobs):
                job_id = stage_ids[stage_index][job_index]
                raw_name = str(
                    raw_job.get("job", raw_job.get("deployment", ""))
                    or (stage.name if len(raw_jobs) == 1 else f"job_{job_index + 1}")
                )
                display_name = str(
                    raw_job.get("displayName", "")
                    or (stage.display_name if len(raw_jobs) == 1 else raw_name)
                    or raw_name
                )
                pool = raw_job.get("pool", stage.agent_pool)
                if isinstance(pool, dict):
                    pool = pool.get("vmImage", pool.get("name", stage.agent_pool))
                job: dict[str, Any] = {
                    "name": display_name,
                    "runs-on": self._resolve_runner(str(pool)),
                }

                needs: list[str] = []
                for dependency_stage in stage.depends_on:
                    dependency_key = str(dependency_stage)
                    needs.extend(
                        stage_lookup.get(
                            dependency_key,
                            stage_lookup.get(
                                self._normalise_job_id(dependency_key),
                                [self._normalise_job_id(dependency_key)],
                            ),
                        )
                    )
                raw_needs = raw_job.get("dependsOn", [])
                if isinstance(raw_needs, str):
                    raw_needs = [raw_needs]
                for dependency_job in raw_needs or []:
                    dependency_name = str(dependency_job)
                    dependency_id = job_name_ids.get(
                        (stage_index, dependency_name),
                        job_name_ids.get(
                            (stage_index, self._normalise_job_id(dependency_name))
                        ),
                    )
                    if dependency_id:
                        needs.append(dependency_id)
                    else:
                        needs.append(self._normalise_job_id(str(dependency_job)))
                if needs:
                    job["needs"] = list(dict.fromkeys(needs))

                stage_condition = self._map_condition(stage.condition) if stage.condition else ""
                job_condition = self._map_condition(str(raw_job.get("condition", "")))
                if stage_condition and job_condition:
                    job["if"] = f"({stage_condition}) && ({job_condition})"
                elif stage_condition or job_condition:
                    job["if"] = stage_condition or job_condition

                raw_environment = raw_job.get("environment")
                environment_name = ""
                if isinstance(raw_environment, dict):
                    environment_name = str(raw_environment.get("name", ""))
                elif raw_environment:
                    environment_name = str(raw_environment)
                elif stage.environment and stage.is_deployment:
                    environment_name = stage.environment.name
                if environment_name:
                    job["environment"] = environment_name

                timeout = raw_job.get("timeoutInMinutes")
                if isinstance(timeout, int) and timeout > 0:
                    job["timeout-minutes"] = timeout
                if raw_job.get("continueOnError") is True:
                    job["continue-on-error"] = True

                combined_env: dict[str, Any] = {}
                for variable in stage.variables:
                    env_name = self._ado_env_name(variable.name)
                    if env_name in combined_env:
                        raise ValueError(
                            f"ADO variables collide after environment-name "
                            f"normalization: {variable.name!r}"
                        )
                    combined_env[env_name] = (
                        f"${{{{ secrets.{variable.name} }}}}"
                        if variable.is_secret else variable.value
                    )
                raw_variables = raw_job.get("variables", {})
                if isinstance(raw_variables, dict):
                    for key, value in raw_variables.items():
                        if isinstance(value, dict):
                            value = value.get("value", "")
                        env_name = self._ado_env_name(str(key))
                        if env_name in combined_env:
                            raise ValueError(
                                f"ADO variables collide after environment-name "
                                f"normalization: {key!r}"
                            )
                        combined_env[env_name] = value
                if combined_env:
                    job["env"] = combined_env

                raw_steps = raw_job.get("steps", [])
                if not isinstance(raw_steps, list):
                    warnings.append(
                        f"Job '{raw_name}' has a non-list steps block; manual review required."
                    )
                    raw_steps = []
                steps: list[dict[str, Any]] = []
                for step in raw_steps:
                    mapped = self._map_step(step, warnings, unsupported)
                    if mapped:
                        steps.append(mapped)
                # Classic inventory may store a workflow task directly as the
                # stage job rather than under a steps collection.
                if not raw_job.get("steps") and any(
                    key in raw_job for key in (
                        "task", "taskName", "script", "bash", "powershell",
                        "__ado2gh_gha_step__",
                    )
                ):
                    mapped = self._map_step(raw_job, warnings, unsupported)
                    if mapped:
                        steps.append(mapped)
                checkout_disabled = any(
                    isinstance(step, dict) and str(step.get("checkout", "")).lower() == "none"
                    for step in raw_steps
                )
                if not checkout_disabled and not any(
                    str(step.get("uses", "")).startswith("actions/checkout@")
                    for step in steps
                ):
                    steps.insert(0, self._defensive_checkout_step())
                if not steps and not raw_job:
                    steps.append({
                        "name": "TODO: add build steps",
                        "run": f'echo "No executable steps were captured for {stage.name}"',
                    })
                    warnings.append(f"Stage '{stage.name}' has no captured executable steps.")
                job["steps"] = steps
                jobs[job_id] = job
        return jobs

    @staticmethod
    def _normalise_job_id(value: str) -> str:
        job_id = re.sub(r"[^A-Za-z0-9_-]", "_", value or "job").lower()
        if not job_id or not re.match(r"^[A-Za-z_]", job_id):
            job_id = "job_" + job_id
        return job_id

    def _build_jobs_from_yaml(
        self,
        meta: PipelineMetadata,
        warnings: list[str],
        unsupported: list[str],
    ) -> dict:
        """Attempt to parse steps from embedded YAML content."""
        jobs: dict[str, Any] = {}
        try:
            parsed = yaml.safe_load(meta.yaml_content)
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
                steps: list[dict] = [self._defensive_checkout_step()]
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
                steps = [self._defensive_checkout_step()]
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
        steps = [self._defensive_checkout_step()]
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
            self._defensive_checkout_step(),
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
        if not isinstance(step, dict):
            unsupported.append("<invalid-step-structure>")
            warnings.append("A non-mapping ADO step could not be converted.")
            return {
                "name": "TODO: migrate invalid ADO step",
                "run": 'echo "Invalid ADO step structure requires manual migration"',
            }
        injected = step.get("__ado2gh_gha_step__")
        if injected is not None:
            # This marker is only created by PipelineConversionExecutor after
            # an LLM proposal or content-addressed operator mapping is checked.
            if not isinstance(injected, dict):
                raise ValueError("Validated GHA step marker must contain a mapping")
            return dict(injected)
        task_name = step.get("task", step.get("taskName", ""))
        display = step.get("displayName", step.get("name", ""))
        inputs = step.get("inputs", {})
        if not isinstance(inputs, dict):
            warnings.append(
                f"Task '{task_name or '<unknown>'}' has a non-mapping inputs block."
            )
            inputs = {}
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

        if env_block and isinstance(env_block, dict):
            gha_step["env"] = dict(env_block)
        elif env_block:
            warnings.append(
                f"Task '{task_name or '<unknown>'}' has a non-mapping env block."
            )
        if step.get("continueOnError") is True:
            gha_step["continue-on-error"] = True
        timeout = step.get("timeoutInMinutes")
        if isinstance(timeout, int) and timeout > 0:
            gha_step["timeout-minutes"] = timeout

        # ── Script / inline run steps ────────────────────────────────────
        script = step.get("script", step.get("bash", step.get("powershell", "")))
        if script and not task_name:
            gha_step["run"] = script
            # Match the ADO Bash/CmdLine POSIX wrapper. GitHub's built-in
            # ``bash`` alias adds ``-e -o pipefail`` and changes success/error
            # behavior for otherwise valid ADO scripts.
            shell = "bash --noprofile --norc {0}"
            if step.get("powershell"):
                shell = "pwsh"
            gha_step["shell"] = shell
            working_directory = step.get("workingDirectory", step.get("working-directory", ""))
            if working_directory:
                gha_step["working-directory"] = working_directory
            if step.get("continueOnError") is True:
                gha_step["continue-on-error"] = True
            timeout = step.get("timeoutInMinutes")
            if isinstance(timeout, int) and timeout > 0:
                gha_step["timeout-minutes"] = timeout
            return gha_step

        if not task_name:
            checkout = step.get("checkout")
            if checkout is not None:
                if str(checkout).lower() == "none":
                    return None
                gha_step["uses"] = "actions/checkout@v4"
                checkout_with: dict[str, Any] = {
                    "persist-credentials": False,
                }
                if str(checkout).lower() != "self":
                    checkout_with["repository"] = str(checkout)
                checkout_options = {
                    "fetchDepth": "fetch-depth",
                    "fetchTags": "fetch-tags",
                    "lfs": "lfs",
                    "submodules": "submodules",
                    "clean": "clean",
                    "path": "path",
                    "ref": "ref",
                    "token": "token",
                }
                for source_key, target_key in checkout_options.items():
                    if source_key in step:
                        checkout_with[target_key] = step[source_key]
                if checkout_with:
                    gha_step["with"] = checkout_with
                return gha_step
            if step.get("publish") is not None:
                return {
                    **({"name": display} if display else {}),
                    "uses": "actions/upload-artifact@v4",
                    "with": {
                        "name": step.get("artifact", "drop"),
                        "path": step.get("publish"),
                    },
                }
            if step.get("download") is not None:
                with_block: dict[str, Any] = {}
                if step.get("artifact"):
                    with_block["name"] = step["artifact"]
                if step.get("path"):
                    with_block["path"] = step["path"]
                result: dict[str, Any] = {
                    **({"name": display} if display else {}),
                    "uses": "actions/download-artifact@v4",
                }
                if with_block:
                    result["with"] = with_block
                return result
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
            elif task_name in {"Bash@3", "CmdLine@2"}:
                gha_step["shell"] = "bash --noprofile --norc {0}"
            working_directory = inputs.get(
                "workingDirectory", inputs.get("workingDir", "")
            )
            if working_directory:
                gha_step["working-directory"] = working_directory
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
        file_path = inputs.get("filePath", "")
        if file_path and task_name in {"Bash@3", "PowerShell@2"}:
            arguments = str(inputs.get("arguments", "")).strip()
            return f"{file_path} {arguments}".strip()

        # Npm tasks.
        if task_name.startswith("Npm"):
            command = inputs.get("command", "install")
            if command == "custom":
                command = inputs.get("customCommand", "") or "install"
            working_dir = inputs.get("workingDir", "")
            if working_dir:
                return f"cd {working_dir} && npm {command}"
            return f"npm {command}"

        # NuGet.
        if task_name.startswith("NuGet"):
            command = inputs.get("command", "restore")
            solution = inputs.get("restoreSolution", inputs.get("solution", ""))
            return f"dotnet {command} {solution}".strip()

        # .NET SDK task. Preserve the ADO command, project glob/path, and
        # explicit arguments rather than mistaking this for setup-dotnet.
        if task_name == "DotNetCoreCLI@2":
            command = str(inputs.get("command", "build"))
            if command == "custom":
                command = str(inputs.get("custom", "")) or "build"
            projects_key = "packagesToPush" if command == "push" else "projects"
            projects = str(inputs.get(projects_key, inputs.get("projects", ""))).strip()
            arguments = str(inputs.get("arguments", "")).strip()
            parts = ["dotnet", command]
            if projects:
                parts.append(projects)
            if arguments:
                parts.append(arguments)
            return " ".join(parts)

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
            # ADO's documented NodeTool@0 default is 6.x.  Always emit it so
            # setup-node cannot fall back to an unrelated runner/repository
            # version selection mechanism.
            w["node-version"] = inputs.get("versionSpec") or "6.x"

        elif task_name == "UsePythonVersion@0":
            # ADO defaults to Python 3.x on x64.  setup-python has different
            # behavior when version is omitted, so both defaults are explicit.
            w["python-version"] = inputs.get("versionSpec") or "3.x"
            w["architecture"] = "x64"

        elif task_name == "UseDotNet@2":
            version = inputs.get("version", "")
            if version:
                w["dotnet-version"] = version

        elif task_name == "DotNetCoreCLI@2":
            version = inputs.get("version", inputs.get("packagesToPush", ""))
            if version:
                w["dotnet-version"] = version

        elif task_name == "JavaToolInstaller@0":
            version = inputs.get("versionSpec", "")
            if version:
                w["java-version"] = version
            # jdkArchitectureOption is an architecture (x64/x86), not a Java
            # distribution. setup-java requires an actual distribution.
            w["distribution"] = "temurin"
            architecture = inputs.get("jdkArchitectureOption", "")
            if architecture:
                w["architecture"] = architecture

        elif task_name == "GoTool@0":
            w["go-version"] = inputs.get("version") or "1.10"

        elif task_name == "Docker@2":
            context = inputs.get("buildContext", ".")
            dockerfile = inputs.get("Dockerfile", inputs.get("dockerfile", ""))
            command = str(inputs.get("command", "buildAndPush")).strip().casefold()
            if command in {"", "buildandpush"}:
                # Docker@2's buildAndPush operation has two target effects.
                # Never silently downgrade it to a build-only action merely
                # because ADO does not expose a separate `push` input.
                push_flag = "true"
            elif command == "build":
                push_flag = "false"
            else:
                push_flag = str(inputs.get("push", "false")).casefold()
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

        elif task_name == "PublishPipelineArtifact@1":
            path = inputs.get("targetPath", inputs.get("path", "."))
            artifact = inputs.get("artifact", inputs.get("artifactName", "drop"))
            w["name"] = artifact
            w["path"] = path

        elif task_name == "DownloadBuildArtifacts@0":
            artifact = inputs.get("artifactName", "drop")
            w["name"] = artifact

        elif task_name == "DownloadPipelineArtifact@2":
            artifact = inputs.get("artifact", inputs.get("artifactName", ""))
            path = inputs.get("path", inputs.get("targetPath", ""))
            if artifact:
                w["name"] = artifact
            if path:
                w["path"] = path

        elif task_name == "Cache@2":
            path = inputs.get("path", "")
            key = inputs.get("key", "")
            restore = inputs.get("restoreKeys", "")
            if path:
                w["path"] = path
            if key:
                w["key"] = key
            if restore:
                w["restore-keys"] = restore

        elif task_name == "PublishTestResults@2":
            fmt = inputs.get("testResultsFormat", "JUnit")
            files = inputs.get("testResultsFiles", "")
            w["reporter"] = fmt.lower()
            if files:
                w["path"] = files

        return w

    # ── condition mapping ─────────────────────────────────────────────────

    @classmethod
    def _map_condition(cls, condition: str) -> str:
        """Translate the supported ADO condition grammar into GHA syntax.

        ADO uses prefix functions (``and(succeeded(), eq(a, b))``). Simple
        regex replacement produced invalid expressions such as ``== (a,b)``;
        this bounded recursive parser preserves nesting and quoting.
        """
        if not condition:
            return ""
        expression = condition.strip()
        if expression.startswith("${{") and expression.endswith("}}"):
            expression = expression[3:-2].strip()
        return cls._map_condition_expression(expression)

    @classmethod
    def _map_condition_expression(cls, expression: str) -> str:
        expression = expression.strip()
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression)
        if match and cls._outer_call_closes_at_end(expression, match.end() - 1):
            name = match.group(1)
            body = expression[match.end():-1]
            raw_args = cls._split_condition_args(body)
            args = [cls._map_condition_expression(arg) for arg in raw_args]
            args = cls._map_build_reason_arguments(name, raw_args, args)
            if name == "succeeded" and not args:
                return "success()"
            if name == "succeededOrFailed" and not args:
                return "(success() || failure())"
            if name == "failed" and not args:
                return "failure()"
            if name == "always" and not args:
                return "always()"
            if name in {"canceled", "cancelled"} and not args:
                return "cancelled()"
            binary = {"eq": "==", "ne": "!="}
            if name in binary and len(args) == 2:
                return f"({args[0]} {binary[name]} {args[1]})"
            joins = {"and": "&&", "or": "||"}
            if name in joins and len(args) >= 2:
                return "(" + f" {joins[name]} ".join(args) + ")"
            if name == "not" and len(args) == 1:
                return f"!({args[0]})"
            if name in {"in", "notIn"} and len(args) >= 2:
                operator = "==" if name == "in" else "!="
                joiner = " || " if name == "in" else " && "
                return "(" + joiner.join(f"{args[0]} {operator} {item}" for item in args[1:]) + ")"
            if name in {"contains", "startsWith", "endsWith"} and len(args) == 2:
                return f"{name}({args[0]}, {args[1]})"
            # Planner prevents unsupported calls from reaching this path unless
            # a checked LLM resolution has already replaced the condition.
            return cls._replace_condition_references(expression)
        return cls._replace_condition_references(expression)

    _BUILD_REASON_EVENTS = {
        "manual": "workflow_dispatch",
        "individualci": "push",
        "batchedci": "push",
        "pullrequest": "pull_request",
        "schedule": "schedule",
    }

    @staticmethod
    def _is_build_reason_reference(value: str) -> bool:
        return re.fullmatch(
            r"variables(?:\.Build\.Reason|\[(['\"])Build\.Reason\1\])",
            value.strip(),
            re.IGNORECASE,
        ) is not None

    @classmethod
    def _map_build_reason_arguments(
        cls,
        function: str,
        raw_args: list[str],
        mapped_args: list[str],
    ) -> list[str]:
        """Translate ADO Build.Reason values only in exact comparisons."""
        result = list(mapped_args)
        reason_indexes = [
            index for index, value in enumerate(raw_args)
            if cls._is_build_reason_reference(value)
        ]
        literal_indexes: list[int] = []
        if function in {"eq", "ne"} and len(raw_args) == 2 \
                and len(reason_indexes) == 1:
            literal_indexes = [1 - reason_indexes[0]]
        elif function in {"in", "notIn"} and reason_indexes == [0]:
            literal_indexes = list(range(1, len(raw_args)))
        for index in literal_indexes:
            match = re.fullmatch(r"(['\"])(.*?)\1", raw_args[index].strip())
            if not match:
                continue
            event = cls._BUILD_REASON_EVENTS.get(match.group(2).casefold())
            if event:
                result[index] = f"'{event}'"
        return result

    @staticmethod
    def _outer_call_closes_at_end(expression: str, open_index: int) -> bool:
        depth = 0
        quote = ""
        escaped = False
        for index, char in enumerate(expression[open_index:], start=open_index):
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index == len(expression) - 1
        return False

    @staticmethod
    def _split_condition_args(body: str) -> list[str]:
        if not body.strip():
            return []
        args: list[str] = []
        start = 0
        depth = 0
        bracket_depth = 0
        quote = ""
        escaped = False
        for index, char in enumerate(body):
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth -= 1
            elif char == "," and depth == 0 and bracket_depth == 0:
                args.append(body[start:index].strip())
                start = index + 1
        args.append(body[start:].strip())
        return args

    @staticmethod
    def _replace_condition_references(expression: str) -> str:
        special = {
            "build.sourcebranch": "github.ref",
            "build.sourcebranchname": "github.ref_name",
            "build.reason": "github.event_name",
        }
        value = expression.strip()
        bracket = re.fullmatch(
            r"variables\[(['\"])([A-Za-z_][A-Za-z0-9_.-]*)\1\]",
            value,
            re.IGNORECASE,
        )
        if bracket:
            name = bracket.group(2)
            mapped = special.get(name.casefold())
            if mapped:
                return mapped
            env_name = PipelineTransformer._ado_env_name(name)
            return f"env.{env_name}"
        dotted_system = re.fullmatch(
            r"variables\.(Build\.(?:SourceBranch|SourceBranchName|Reason))",
            value,
            re.IGNORECASE,
        )
        if dotted_system:
            return special[dotted_system.group(1).casefold()]
        dotted_variable = re.fullmatch(
            r"variables\.([A-Za-z_][A-Za-z0-9_]*)",
            value,
            re.IGNORECASE,
        )
        if dotted_variable:
            return (
                "env."
                + PipelineTransformer._ado_env_name(dotted_variable.group(1))
            )
        bracket_parameter = re.fullmatch(
            r"parameters\[(['\"])([A-Za-z_][A-Za-z0-9_.-]*)\1\]",
            value,
            re.IGNORECASE,
        )
        if bracket_parameter:
            name = bracket_parameter.group(2)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                return f"inputs.{name}"
            escaped = name.replace("'", "''")
            return f"inputs['{escaped}']"
        dotted_parameter = re.fullmatch(
            r"parameters\.([A-Za-z_][A-Za-z0-9_]*)",
            value,
            re.IGNORECASE,
        )
        if dotted_parameter:
            return f"inputs.{dotted_parameter.group(1)}"
        if value.casefold() == "true":
            return "true"
        if value.casefold() == "false":
            return "false"
        return expression

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
        # Preserve an explicit/self-hosted label. The planner marks it as an
        # external runner mapping, avoiding a silent execution on Ubuntu.
        return pool_name

    # ── environment block helper ──────────────────────────────────────────

    # ── ADO -> GHA expression rewriter ────────────────────────────────────

    # ${{ parameters.X }}  ->  ${{ inputs.X }}
    _RE_ADO_PARAM = re.compile(
        r"\$\{\{\s*parameters\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
    )
    _RE_ADO_PARAM_INDEX = re.compile(
        r"\$\{\{\s*parameters\[(['\"])(.+?)\1\]\s*\}\}"
    )
    # $(X) macro  ->  ${{ env.X }}  (only for word-shaped names; leave shell
    # command substitutions like $(date) alone by checking against env keys).
    _RE_ADO_MACRO = re.compile(
        r"\$\(([A-Za-z_][A-Za-z0-9_.-]*)\)"
    )

    @classmethod
    def _rewrite_ado_expression_string(cls, s: str, env_keys: set) -> str:
        if not isinstance(s, str) or "$" not in s:
            return s
        s = cls._RE_ADO_PARAM.sub(r"${{ inputs.\1 }}", s)
        def _parameter_index(match: "re.Match") -> str:
            name = match.group(2)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                return f"${{{{ inputs.{name} }}}}"
            escaped = name.replace("'", "''")
            return f"${{{{ inputs['{escaped}'] }}}}"
        s = cls._RE_ADO_PARAM_INDEX.sub(_parameter_index, s)
        # Only rewrite $(X) when X is in the env block — that's how we know
        # it's an ADO variable macro and not a bash command substitution.
        if env_keys:
            def _macro(m: "re.Match") -> str:
                name = m.group(1)
                actual = next(
                    (
                        key for key in env_keys
                        if str(key) == cls._ado_env_name(name)
                    ),
                    None,
                )
                if actual is None:
                    return m.group(0)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(actual)):
                    return f"${{{{ env.{actual} }}}}"
                escaped = str(actual).replace("'", "''")
                return f"${{{{ env['{escaped}'] }}}}"
            s = cls._RE_ADO_MACRO.sub(_macro, s)
        return s

    @classmethod
    def _rewrite_ado_expressions_inplace(cls, obj, env_keys: set) -> None:
        """Walk a workflow dict in place, rewriting ADO expressions in
        every string value."""
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, str):
                    obj[k] = cls._rewrite_ado_expression_string(v, env_keys)
                else:
                    cls._rewrite_ado_expressions_inplace(v, env_keys)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str):
                    obj[i] = cls._rewrite_ado_expression_string(v, env_keys)
                else:
                    cls._rewrite_ado_expressions_inplace(v, env_keys)

    @staticmethod
    def _ado_env_name(name: str) -> str:
        """Apply Azure Pipelines' script environment variable projection."""
        projected = str(name).replace(".", "_").upper()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", projected):
            raise ValueError(
                f"ADO variable {name!r} has no safe GitHub environment name"
            )
        if projected.startswith(("GITHUB_", "RUNNER_", "ACTIONS_")):
            raise ValueError(
                f"ADO variable {name!r} collides with a reserved GitHub name"
            )
        return projected

    @classmethod
    def _build_env_block(cls, meta: PipelineMetadata) -> dict[str, str]:
        env: dict[str, str] = {}
        for v in meta.variables:
            # Secrets are never workflow-global or job-global.  They may be
            # introduced only by an exact approved action input; otherwise
            # every third-party action would inherit every migrated secret.
            if not v.is_secret:
                name = cls._ado_env_name(v.name)
                if name in env:
                    raise ValueError(
                        f"ADO variables collide after environment-name "
                        f"normalization: {v.name!r}"
                    )
                env[name] = v.value
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
