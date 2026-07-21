"""Deterministic planner for ADO pipeline conversion."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Optional

import yaml

from ado2gh.models import PipelineMetadata, PipelineType
from ado2gh.pipelines.llm import redact_for_llm
from ado2gh.pipelines.pev_types import (
    ConversionMode,
    ConversionPlan,
    PlanAmbiguity,
)


_SUPPORTED_CONDITION_FUNCTIONS = {
    "succeeded", "succeededOrFailed", "failed", "always", "canceled",
    "cancelled", "eq", "ne", "and", "or", "not", "in", "notIn",
    "contains", "startsWith", "endsWith",
}
_GITHUB_RUNNERS = re.compile(
    r"^(?:ubuntu|windows|macos)-(?:latest|[0-9][A-Za-z0-9.-]*)$",
    re.IGNORECASE,
)


class PipelineConversionPlanner:
    """Classify source constructs before any workflow is generated.

    The plan is stable for identical normalized metadata.  It decides which
    constructs use deterministic rules, which may be proposed to a bounded LLM,
    and which require external/operator input that an LLM cannot know.
    """

    # Bump whenever deterministic output or an approval boundary changes so a
    # receipt created under older fidelity rules cannot be reused silently.
    RULESET_VERSION = "pipeline-pev-10"

    def plan(self, meta: PipelineMetadata) -> ConversionPlan:
        # Imported lazily to keep the rules consumable without introducing a
        # transformer/planner import cycle at module import time.
        from ado2gh.pipelines.transformer import ADO_TASK_MAP

        fingerprint = self._source_fingerprint(meta)
        secrets = [
            variable.value for variable in meta.variables
            if variable.is_secret and variable.value
        ]
        ambiguities: list[PlanAmbiguity] = []
        seen: set[tuple[str, tuple[Any, ...]]] = set()
        deterministic_steps = 0
        expected_steps = 0
        notices: list[str] = []
        declared_condition_variables = {
            variable.name.casefold()
            for variable in meta.variables
            if not variable.is_secret and variable.name
        }
        declared_parameters = {
            str(parameter.get("name", ""))
            for parameter in meta.parameters
            if str(parameter.get("name", "")).strip()
        }
        parsed = self._safe_parse_yaml(meta.yaml_content)

        def add(
            kind: str,
            location: Iterable[Any],
            rationale: str,
            source: Any = None,
            *,
            llm_eligible: bool = False,
            required: bool = True,
        ) -> None:
            path = tuple(location)
            dedupe = (kind, path)
            if dedupe in seen:
                return
            seen.add(dedupe)
            safe_source = redact_for_llm(source, secrets)
            identity = json.dumps(
                {"kind": kind, "location": list(path), "source": safe_source},
                sort_keys=True,
                default=str,
            )
            ambiguity_id = "amb-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            ambiguities.append(PlanAmbiguity(
                ambiguity_id=ambiguity_id,
                kind=kind,
                location=path,
                rationale=rationale,
                source=safe_source,
                llm_eligible=llm_eligible,
                required=required,
            ))

        def check_environment_variable_scope(
            variables: Iterable[Any],
            location: tuple[Any, ...],
        ) -> None:
            projected: dict[str, str] = {}
            for index, variable in enumerate(variables):
                name = str(getattr(variable, "name", ""))
                env_name = name.replace(".", "_").upper()
                if (
                    not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env_name)
                    or env_name.startswith(("GITHUB_", "RUNNER_", "ACTIONS_"))
                ):
                    add(
                        "unsupported_variable_environment_name",
                        location + (index, "name"),
                        "ADO variable name cannot be represented as a safe "
                        "GitHub environment variable using ADO's uppercase, "
                        "period-to-underscore projection.",
                        {"name": name},
                    )
                    continue
                prior = projected.get(env_name)
                if prior is not None:
                    add(
                        "variable_environment_collision",
                        location + (index, "name"),
                        "Multiple ADO variables collapse to the same script "
                        "environment name after uppercase/period normalization.",
                        {"names": [prior, name], "environment_name": env_name},
                    )
                else:
                    projected[env_name] = name

        check_environment_variable_scope(meta.variables, ("variables",))

        # External mappings are intentionally never delegated to an LLM: the
        # model cannot know enterprise runner labels, credentials, reviewers,
        # or organization policy.
        for index, group in enumerate(meta.variable_groups):
            add(
                "variable_group_mapping", ("variable_groups", index),
                "ADO variable group must be mapped to GitHub variables/secrets by policy.",
                {"name": group.get("name", ""), "variables": group.get("variables", [])},
            )
        for index, variable in enumerate(meta.variables):
            if variable.is_secret:
                add(
                    "direct_secret_mapping", ("variables", index),
                    "ADO secret variable requires a verified GitHub repository "
                    "secret with the same deterministic workflow name.",
                    {"name": variable.name},
                )
            elif any(marker in variable.value for marker in ("$[", "${{", "$(")):
                add(
                    "variable_expression_semantics",
                    ("variables", index, "value"),
                    "ADO compile-time, runtime, and recursively expanded macro "
                    "variable expressions are not equivalent to a GitHub "
                    "workflow env mapping.",
                    {"name": variable.name, "value": variable.value},
                )

        for index, parameter in enumerate(meta.parameters):
            name = str(parameter.get("name", ""))
            parameter_type = str(
                parameter.get("type", "string") or "string"
            ).casefold()
            default = parameter.get("default")
            values = parameter.get("values")
            supported = parameter_type in {"string", "boolean", "bool", "number"}
            if parameter_type == "string":
                supported = supported and (
                    default is None or isinstance(default, str)
                )
                if values is not None:
                    supported = supported and isinstance(values, list) \
                        and bool(values) \
                        and all(isinstance(value, str) for value in values)
            elif parameter_type in {"boolean", "bool"}:
                supported = supported and (
                    default is None or isinstance(default, bool)
                ) and values is None
            elif parameter_type == "number":
                supported = supported and (
                    default is None
                    or (
                        isinstance(default, (int, float))
                        and not isinstance(default, bool)
                        and math.isfinite(float(default))
                    )
                ) and values is None
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) \
                    or not supported:
                add(
                    "unsupported_parameter_semantics",
                    ("parameters", index),
                    "Only scalar string/boolean/number parameters with exact "
                    "type-correct defaults are supported; structured/template "
                    "parameters and non-string choices require manual redesign.",
                    parameter,
                )
        for index, connection in enumerate(meta.service_connections):
            add(
                "service_connection_mapping", ("service_connections", index),
                "ADO service connection requires an approved GitHub secret or OIDC mapping.",
                {"name": connection.get("name", ""), "type": connection.get("type", "")},
            )
        for index, environment in enumerate(meta.environments):
            if environment.required_approvers or environment.pre_deploy_checks or environment.post_deploy_checks:
                add(
                    "environment_protection", ("environments", index),
                    "GitHub environment protection and reviewers must be configured outside workflow YAML.",
                    {
                        "name": environment.name,
                        "approver_count": len(environment.required_approvers),
                        "pre_check_count": len(environment.pre_deploy_checks),
                        "post_check_count": len(environment.post_deploy_checks),
                    },
                )

        if meta.parameters and (
            meta.trigger_branches
            or meta.trigger_pr_branches
            or meta.trigger_schedules
        ):
            add(
                "parameter_trigger_semantics",
                ("parameters", "non_manual_triggers"),
                "ADO compile-time parameter defaults apply to automatic runs, "
                "while GitHub workflow_dispatch inputs do not exist on push, "
                "pull_request, or schedule events. Automatic conversion is "
                "blocked until those values are modeled without changing "
                "boolean, empty-string, or numeric semantics.",
                {
                    "parameter_count": len(meta.parameters),
                    "push": bool(meta.trigger_branches),
                    "pull_request": bool(meta.trigger_pr_branches),
                    "schedule": bool(meta.trigger_schedules),
                },
            )

        # These normalized ADO trigger behaviors do not have an exact mapping
        # in the current deterministic transformer.  They deliberately use a
        # non-approvable ambiguity kind: an attestation cannot make broadened
        # or differently queued GitHub events semantically equivalent.
        if isinstance(parsed, Mapping) and "trigger" not in parsed:
            add(
                "unsupported_trigger_semantics",
                ("yaml", "trigger"),
                "The YAML trigger is omitted. Azure DevOps may imply CI on all "
                "branches unless an organization/project/UI setting disables "
                "it, and that effective setting is not inventoried. Trigger "
                "conversion is blocked rather than silently removing CI.",
                {"trigger": "omitted", "effective_setting": "unknown"},
            )
        for field_name, values in (
            ("trigger_branches", meta.trigger_branches),
            ("trigger_pr_branches", meta.trigger_pr_branches),
        ):
            for index, branch in enumerate(values):
                if not self._trigger_branch_literal_is_exact(str(branch)):
                    add(
                        "unsupported_trigger_semantics",
                        (field_name, index),
                        "ADO and GitHub branch/tag glob grammars differ. Only "
                        "exact, ref-safe literal branch names are emitted; "
                        "wildcards, GitHub metacharacters, and refs/tags filters "
                        "require an explicit trigger redesign.",
                        {"filter": branch},
                    )
        if (
            str(meta.repo_type or "").casefold() == "tfsgit"
            and (
                meta.trigger_pr_branches
                or meta.trigger_pr_branch_excludes
                or meta.trigger_pr_path_includes
                or meta.trigger_pr_path_excludes
                or (isinstance(parsed, Mapping) and parsed.get("pr") not in (None, "none", False))
            )
        ):
            add(
                "unsupported_trigger_semantics",
                ("yaml", "pr"),
                "Azure Repos Git ignores YAML pr triggers and uses branch-policy "
                "build validation. Emitting pull_request would activate dormant "
                "source syntax, so exact branch-policy inventory/translation is "
                "required.",
                {"repo_type": meta.repo_type, "pr": parsed.get("pr") if isinstance(parsed, Mapping) else None},
            )
        if meta.trigger_batch:
            add(
                "unsupported_trigger_semantics",
                ("trigger_batch",),
                "ADO CI batch=true queues changes using ADO batching semantics. "
                "GitHub concurrency cancellation/queuing is not equivalent, so "
                "this pipeline requires a redesigned trigger before migration.",
                {"batch": True},
            )
        for field_name, label, values in (
            (
                "trigger_branch_excludes",
                "ADO CI branch exclusions",
                meta.trigger_branch_excludes,
            ),
            (
                "trigger_path_includes",
                "ADO CI path inclusions",
                meta.trigger_path_includes,
            ),
            (
                "trigger_path_excludes",
                "ADO CI path exclusions",
                meta.trigger_path_excludes,
            ),
            (
                "trigger_pr_branch_excludes",
                "ADO PR branch exclusions",
                meta.trigger_pr_branch_excludes,
            ),
            (
                "trigger_pr_path_includes",
                "ADO PR path inclusions",
                meta.trigger_pr_path_includes,
            ),
            (
                "trigger_pr_path_excludes",
                "ADO PR path exclusions",
                meta.trigger_pr_path_excludes,
            ),
        ):
            if values:
                add(
                    "unsupported_trigger_semantics",
                    (field_name,),
                    f"{label} are retained in normalized metadata but are not "
                    "emitted because ADO and GitHub filter grammars have not "
                    "been proven equivalent. Redesign and re-plan the trigger.",
                    {"filters": list(values)},
                )
        if meta.trigger_pr_auto_cancel is True:
            add(
                "unsupported_trigger_semantics",
                ("trigger_pr_auto_cancel",),
                "ADO PR autoCancel=true cancels superseded validation runs. "
                "No exact GitHub concurrency mapping is emitted, so the PR "
                "trigger must be redesigned before migration.",
                {"auto_cancel": True},
            )
        if meta.trigger_pr_drafts is False:
            add(
                "unsupported_trigger_semantics",
                ("trigger_pr_drafts",),
                "ADO PR drafts=false suppresses draft validation runs. The "
                "generated GitHub event does not preserve that behavior, so "
                "the PR trigger must be redesigned before migration.",
                {"drafts": False},
            )

        for index, note in enumerate(meta.migration_notes):
            lower_note = note.lower()
            if any(marker in lower_note for marker in (
                "manual review", "manual conversion", "parity review",
                "could not parse", "branch excludes", "path filters",
                "auto-selected", "configured yaml path",
            )):
                add(
                    "source_semantics_review", ("migration_notes", index),
                    note,
                    None,
                )

        for index, schedule in enumerate(meta.trigger_schedules):
            location = ("trigger_schedules", index)
            if not isinstance(schedule, Mapping):
                add(
                    "source_semantics_review",
                    location,
                    "ADO schedule metadata is malformed and cannot be converted safely.",
                    schedule,
                )
                continue
            branch_filters = schedule.get(
                "branch_filters", schedule.get("branch", [])
            )
            excluded_branches = schedule.get("excluded_branches", [])
            unsupported_semantics: list[str] = []
            if branch_filters:
                unsupported_semantics.append(
                    "branch selection (GitHub scheduled workflows run only from "
                    "the repository default branch)"
                )
            if excluded_branches:
                unsupported_semantics.append(
                    "branch exclusions (GitHub schedule events have no branch filters)"
                )
            # ADO's default `always: false` runs only after source changes.
            # GitHub cron events run without an equivalent change-detection
            # gate. `always: true` is therefore the only directly equivalent
            # normalized value.
            if schedule.get("always") is not True:
                unsupported_semantics.append(
                    "changes-only execution (`always: false`), which GitHub cron "
                    "does not implement"
                )
            if unsupported_semantics:
                add(
                    "source_semantics_review",
                    location,
                    "ADO schedule requires parity review for "
                    + "; ".join(unsupported_semantics)
                    + ". The cron expression is preserved deterministically, but "
                    "these trigger semantics are not.",
                    {
                        "cron": schedule.get("cron", ""),
                        "branch_filters": branch_filters,
                        "excluded_branches": excluded_branches,
                        "always": schedule.get("always"),
                    },
                )

        if meta.pipeline_type in {PipelineType.CLASSIC, PipelineType.RELEASE}:
            add(
                "classic_pipeline_semantics", ("pipeline_type",),
                "GUI pipeline semantics and external approvals require operator parity review.",
                {"pipeline_type": meta.pipeline_type.value},
                required=True,
            )

        if isinstance(parsed, Mapping):
            for location, rationale, source in \
                    self._raw_yaml_unsupported_constructs(parsed):
                add(
                    "unsupported_source_construct",
                    ("yaml",) + location,
                    rationale,
                    source,
                )
            if parsed.get("extends"):
                add(
                    "external_template", ("yaml", "extends"),
                    "The referenced template must be fetched and expanded before safe conversion.",
                    parsed.get("extends"),
                )
            resources = parsed.get("resources")
            if resources:
                add(
                    "external_resource", ("yaml", "resources"),
                    "Pipeline resources need explicit repository/pipeline/container mappings.",
                    resources,
                )
            if parsed.get("container") or parsed.get("services"):
                add(
                    "container_runtime", ("yaml", "container"),
                    "Container and service topology is not represented by the deterministic converter.",
                    {
                        "container": parsed.get("container"),
                        "services": parsed.get("services"),
                    },
                )

        for stage_index, stage in enumerate(meta.stages):
            stage_path = ("stages", stage_index)
            check_environment_variable_scope(
                stage.variables, stage_path + ("variables",)
            )
            if meta.pipeline_type == PipelineType.YAML and stage.is_deployment:
                add(
                    "deployment_environment_source_checks_unavailable",
                    stage_path + ("environment",),
                    "ADO YAML environment approvals and checks are not "
                    "exhaustively inventoried by resource ID; deployment is "
                    "blocked rather than targeting an unprotected environment.",
                    {
                        "environment": (
                            stage.environment.name if stage.environment else ""
                        ),
                    },
                )
            if stage.condition and not self.condition_is_supported(
                stage.condition,
                declared_condition_variables,
                declared_parameters,
            ):
                add(
                    "unsupported_condition", stage_path + ("condition",),
                    "The condition uses ADO functions outside the deterministic condition grammar.",
                    stage.condition,
                    llm_eligible=True,
                )
            if stage.agent_pool and not _GITHUB_RUNNERS.match(stage.agent_pool):
                add(
                    "runner_mapping", stage_path + ("agent_pool",),
                    "ADO/self-hosted pool needs an organization-approved GitHub runner label.",
                    stage.agent_pool,
                )

            for job_index, job in enumerate(stage.jobs):
                job_path = stage_path + ("jobs", job_index)
                if not isinstance(job, Mapping):
                    add(
                        "unknown_job", job_path,
                        "Job structure is not a mapping and cannot be converted safely.",
                        job,
                    )
                    continue
                if meta.pipeline_type == PipelineType.YAML \
                        and job.get("deployment") is not None:
                    add(
                        "deployment_environment_source_checks_unavailable",
                        job_path + ("environment",),
                        "ADO YAML deployment-job environment approvals/checks "
                        "are not bound to the pipeline inventory.",
                        {
                            "deployment": job.get("deployment"),
                            "environment": job.get("environment"),
                        },
                    )
                if job.get("template"):
                    add(
                        "external_template", job_path + ("template",),
                        "Job template must be fetched and expanded before conversion.",
                        job.get("template"),
                    )
                    continue
                if job.get("container") or job.get("services"):
                    add(
                        "container_runtime", job_path + ("container",),
                        "Job container/services require an explicit GitHub Actions mapping.",
                        {"container": job.get("container"), "services": job.get("services")},
                    )
                raw_pool = job.get("pool")
                if isinstance(raw_pool, Mapping):
                    raw_pool = raw_pool.get("vmImage", raw_pool.get("name", ""))
                effective_pool = str(raw_pool or stage.agent_pool or "")
                if raw_pool and not _GITHUB_RUNNERS.match(str(raw_pool)):
                    add(
                        "runner_mapping", job_path + ("pool",),
                        "ADO/self-hosted job pool needs an organization-approved GitHub runner label.",
                        raw_pool,
                    )
                if job.get("strategy"):
                    add(
                        "job_strategy", job_path + ("strategy",),
                        "ADO job strategy/matrix semantics are not yet deterministically preserved.",
                        job.get("strategy"),
                    )
                job_condition = job.get("condition", "")
                if job_condition and not self.condition_is_supported(
                    str(job_condition),
                    declared_condition_variables,
                    declared_parameters,
                ):
                    add(
                        "unsupported_condition", job_path + ("condition",),
                        "The job condition is outside the deterministic condition grammar.",
                        job_condition,
                        llm_eligible=True,
                    )

                steps = job.get("steps")
                if steps is None and self._looks_like_step(job):
                    steps = [job]
                    steps_base = job_path
                else:
                    steps_base = job_path + ("steps",)
                if steps is None:
                    continue
                if not isinstance(steps, list):
                    add(
                        "unknown_step_collection", steps_base,
                        "Job steps are not a list.",
                        steps,
                    )
                    continue

                if (
                    meta.pipeline_type == PipelineType.YAML
                    and bool(meta.yaml_content.strip())
                    and not any(
                        isinstance(item, Mapping) and item.get("checkout") is not None
                        for item in steps
                    )
                ):
                    add(
                        "checkout_source_settings_unavailable",
                        job_path + ("implicit_checkout",),
                        "The job relies on ADO's implicit checkout, but effective "
                        "clean/fetch-depth values may come from pipeline UI settings "
                        "that are not inventoried. The generated defensive checkout "
                        "cannot be claimed semantically equivalent.",
                        {"checkout": "implicit", "clean": "unknown", "fetchDepth": "unknown"},
                    )

                for step_index, step in enumerate(steps):
                    location = (
                        job_path if steps_base == job_path
                        else steps_base + (step_index,)
                    )
                    if not isinstance(step, Mapping):
                        expected_steps += 1
                        add(
                            "unknown_step", location,
                            "Step structure is not a mapping.",
                            step,
                            llm_eligible=True,
                        )
                        continue
                    if step.get("enabled", True) is False:
                        continue
                    if step.get("template"):
                        add(
                            "external_template", location + ("template",),
                            "Step template must be fetched and expanded before conversion.",
                            step.get("template"),
                        )
                        continue
                    task_name = str(step.get("task", step.get("taskName", "")))
                    recognized_shape = (
                        task_name in ADO_TASK_MAP
                        or any(key in step for key in (
                            "script", "bash", "powershell", "checkout", "publish", "download",
                        ))
                    )
                    if (
                        recognized_shape
                        and step.get("condition")
                        and not self.condition_is_supported(
                            str(step["condition"]),
                            declared_condition_variables,
                            declared_parameters,
                        )
                    ):
                        add(
                            "unsupported_condition", location + ("condition",),
                            "The step condition is outside the deterministic condition grammar.",
                            step.get("condition"),
                            llm_eligible=True,
                        )
                    expected_steps += 1
                    if task_name:
                        if task_name in ADO_TASK_MAP:
                            inputs = step.get("inputs", {})
                            inputs = inputs if isinstance(inputs, Mapping) else {}
                            authentication_input = any(
                                str(key).casefold() in {
                                    "azuresubscription",
                                    "connectedservicename",
                                    "connectedservicenamearm",
                                    "containerregistry",
                                    "dockerregistryendpoint",
                                    "publishregistry",
                                }
                                for key in inputs
                            )
                            docker_command = str(
                                inputs.get("command", "buildAndPush")
                            ).strip()
                            docker_command_key = docker_command.casefold()
                            if not self._task_step_shape_is_exact(step):
                                add(
                                    "unknown_task", location,
                                    f"{task_name} uses step control properties "
                                    "outside the exact deterministic grammar.",
                                    step,
                                )
                            elif task_name == "CmdLine@2" and not \
                                    self._is_posix_runner(effective_pool):
                                add(
                                    "unknown_task", location,
                                    "CmdLine@2 shell semantics depend on the "
                                    "agent OS; deterministic conversion is "
                                    "allowed only for an explicit POSIX runner.",
                                    step,
                                )
                            elif task_name == "PowerShell@2":
                                add(
                                    "unknown_task", location,
                                    "PowerShell@2 host/error semantics differ "
                                    "between ADO Windows PowerShell, ADO pwsh, "
                                    "and GitHub shell wrappers; approve an exact "
                                    "workflow step with the intended engine.",
                                    step,
                                )
                            elif task_name == "Bash@3" and not \
                                    self._is_posix_runner(effective_pool):
                                add(
                                    "unknown_task", location,
                                    "Bash@3 uses WSL on ADO Windows agents but "
                                    "Git for Windows Bash on GitHub runners; "
                                    "deterministic conversion requires an "
                                    "explicit POSIX runner.",
                                    step,
                                )
                            elif task_name == "Docker@2":
                                if docker_command_key == "build":
                                    add(
                                        "unknown_task", location,
                                        "Docker@2 input semantics exceed the exact "
                                        "deterministic grammar; approve the complete "
                                        "build step.",
                                        step,
                                    )
                                elif docker_command_key == "buildandpush":
                                    add(
                                        "docker_publish_configuration",
                                        location,
                                        "Docker@2 buildAndPush requires an approved "
                                        "GitHub registry/authentication and image-tag "
                                        "mapping. The generated action keeps push=true.",
                                        {
                                            "command": "buildAndPush",
                                            "container_registry": inputs.get(
                                                "containerRegistry", ""
                                            ),
                                            "repository": inputs.get("repository", ""),
                                            "tags": inputs.get("tags", ""),
                                        },
                                    )
                                    add(
                                        "unknown_task", location,
                                        "Docker@2 buildAndPush requires one exact "
                                        "approved authentication-and-publish step; "
                                        "a secret-name mapping alone cannot make the "
                                        "generated workflow runnable.",
                                        step,
                                    )
                                else:
                                    add(
                                        "unknown_task", location,
                                        f"Docker@2 command '{docker_command}' needs explicit registry/auth semantics.",
                                        step,
                                        llm_eligible=docker_command_key not in {"login", "logout"},
                                    )
                            elif task_name in {"PipAuthenticate@1", "NpmAuthenticate@0"}:
                                add(
                                    "package_authentication", location,
                                    f"{task_name} requires an approved GitHub registry credential mapping.",
                                    {"task": task_name},
                                )
                                add(
                                    "unknown_task", location,
                                    f"{task_name} requires an exact approved workflow "
                                    "step that applies the mapped credential.",
                                    step,
                                )
                            elif task_name in {"Bash@3", "PowerShell@2", "CmdLine@2"} and not (
                                inputs.get("script") or inputs.get("filePath")
                            ):
                                add(
                                    "unknown_task", location,
                                    f"{task_name} has neither inline script nor file path.",
                                    step,
                                    llm_eligible=True,
                                )
                            elif task_name.startswith("Azure") or authentication_input:
                                add(
                                    "unknown_task", location,
                                    f"{task_name} requires an exact approved workflow "
                                    "step containing both authentication and the "
                                    "intended operation; external secret existence "
                                    "alone is insufficient.",
                                    step,
                                )
                            elif self._task_is_exactly_supported(task_name, inputs):
                                deterministic_steps += 1
                            else:
                                add(
                                    "unknown_task", location,
                                    f"{task_name} has command/input semantics outside "
                                    "the deterministic fidelity grammar and requires "
                                    "an exact approved workflow step.",
                                    step,
                                )
                            if task_name.startswith("Azure"):
                                notices.append(
                                    f"Task {task_name} requires a separate azure/login or federated credential setup."
                                )
                        else:
                            add(
                                "unknown_task", location,
                                f"No deterministic GitHub Actions rule exists for ADO task '{task_name}'.",
                                step,
                                llm_eligible=True,
                            )
                    elif step.get("script") is not None \
                            or step.get("bash") is not None \
                            or step.get("powershell") is not None:
                        if not self._shorthand_step_is_exactly_supported(
                            step, effective_pool
                        ):
                            add(
                                "unknown_step", location,
                                "Script shorthand has platform-dependent or "
                                "unpreserved control properties and requires "
                                "an exact approved workflow step.",
                                step,
                            )
                        else:
                            deterministic_steps += 1
                    elif step.get("checkout") is not None:
                        if not self._shorthand_step_is_exactly_supported(
                            step, effective_pool
                        ):
                            add(
                                "unknown_step", location,
                                "Checkout shorthand contains options outside "
                                "the exact deterministic grammar.",
                                step,
                            )
                            continue
                        deterministic_steps += 1
                        checkout = str(step.get("checkout", ""))
                        if checkout.casefold() != "none" and (
                            "clean" not in step or "fetchDepth" not in step
                        ):
                            add(
                                "checkout_source_settings_unavailable",
                                location + ("effective_source_settings",),
                                "ADO checkout clean/fetchDepth may be inherited "
                                "from pipeline UI settings. Both values must be "
                                "inventoried or explicitly declared before an "
                                "equivalent checkout can be approved.",
                                {
                                    "clean": step.get("clean", "unknown"),
                                    "fetchDepth": step.get("fetchDepth", "unknown"),
                                },
                            )
                        if checkout.lower() not in {"self", "none"}:
                            add(
                                "external_repository_checkout", location,
                                "External repository checkout needs an explicit GitHub repository/ref/token mapping.",
                                {"checkout": checkout, "ref": step.get("ref", "")},
                            )
                    elif step.get("publish") is not None or step.get("download") is not None:
                        if self._shorthand_step_is_exactly_supported(
                            step, effective_pool
                        ):
                            deterministic_steps += 1
                        else:
                            add(
                                "unknown_step", location,
                                "Artifact shorthand contains selectors or "
                                "control properties that are not preserved.",
                                step,
                            )
                    else:
                        add(
                            "unknown_step", location,
                            "Step has no recognized task, script, checkout, or template shape.",
                            step,
                            llm_eligible=True,
                        )

        for index, task_name in enumerate(meta.unsupported_tasks):
            if not any(
                a.kind == "unknown_task" and isinstance(a.source, Mapping)
                and a.source.get("task") == task_name
                for a in ambiguities
            ):
                add(
                    "unlocated_unsupported_task", ("unsupported_tasks", index),
                    "Persisted inventory names an unsupported task but no source step is available for resolution.",
                    task_name,
                )

        if not meta.stages or not any(stage.jobs for stage in meta.stages):
            add(
                "missing_pipeline_body", ("stages",),
                "No executable source jobs/steps were captured; a placeholder cannot prove parity.",
                None,
            )

        if ambiguities:
            mode = (
                ConversionMode.HYBRID
                if any(a.llm_eligible for a in ambiguities)
                else ConversionMode.MANUAL
            )
        else:
            mode = ConversionMode.DETERMINISTIC

        plan_seed = f"{fingerprint}:{self.RULESET_VERSION}"
        plan_id = "plan-" + hashlib.sha256(plan_seed.encode("utf-8")).hexdigest()[:24]
        return ConversionPlan(
            plan_id=plan_id,
            source_fingerprint=fingerprint,
            ruleset_version=self.RULESET_VERSION,
            pipeline_id=meta.pipeline_id,
            pipeline_name=meta.pipeline_name,
            mode=mode,
            deterministic_steps=deterministic_steps,
            expected_executable_steps=expected_steps,
            ambiguities=tuple(ambiguities),
            notices=tuple(dict.fromkeys(notices)),
        )

    @staticmethod
    def _looks_like_step(value: Mapping[str, Any]) -> bool:
        return any(
            key in value
            for key in (
                "task", "taskName", "script", "bash", "powershell",
                "checkout", "publish", "download",
            )
        )

    @staticmethod
    def _task_is_exactly_supported(
        task_name: str, inputs: Mapping[str, Any]
    ) -> bool:
        """Allow only task/input combinations the transformer preserves exactly."""
        allowed: dict[str, set[str]] = {
            "NodeTool@0": {"versionSpec"},
            "UsePythonVersion@0": {"versionSpec"},
            "UseDotNet@2": {"version"},
            # JavaToolInstaller's preinstalled/download source and vendor are
            # not equivalent to setup-java's required distribution contract.
            "JavaToolInstaller@0": set(),
            "GoTool@0": {"version"},
            "CmdLine@2": {"script", "workingDirectory", "workingDir"},
            "Bash@3": {
                "script", "filePath", "arguments", "workingDirectory",
                "workingDir",
            },
            "PowerShell@2": {
                "script", "filePath", "arguments", "workingDirectory",
                "workingDir",
            },
        }
        permitted = allowed.get(task_name)
        if permitted is None or any(str(key) not in permitted for key in inputs):
            return False
        if task_name == "JavaToolInstaller@0":
            return False
        if task_name in {"NodeTool@0", "UsePythonVersion@0", "GoTool@0"}:
            key = {
                "NodeTool@0": "versionSpec",
                "UsePythonVersion@0": "versionSpec",
                "GoTool@0": "version",
            }[task_name]
            value = inputs.get(key)
            return value in (None, "") or PipelineConversionPlanner._literal_tool_version(value)
        if task_name == "UseDotNet@2":
            return PipelineConversionPlanner._literal_tool_version(
                inputs.get("version")
            )
        if task_name in {"CmdLine@2", "Bash@3", "PowerShell@2"}:
            # filePath + free-form argument strings cannot be translated into
            # an argv-safe GitHub shell invocation without changing quoting.
            return bool(inputs.get("script")) and not inputs.get("filePath")
        return True

    @staticmethod
    def _literal_tool_version(value: Any) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        return bool(re.fullmatch(
            r"[0-9]+(?:\.(?:[0-9]+|[xX*])){0,2}(?:[-+][0-9A-Za-z.-]+)?",
            value.strip(),
        ))

    @staticmethod
    def _trigger_branch_literal_is_exact(value: str) -> bool:
        """Accept only names whose ADO/GitHub meaning is the same literal ref."""
        branch = str(value or "").strip()
        if not branch or branch.startswith("refs/tags/"):
            return False
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            return False
        if (
            branch.startswith(("/", "."))
            or branch.endswith(("/", ".", ".lock"))
            or "//" in branch
            or ".." in branch
            or "@{" in branch
            or any(part in {"", ".", ".."} for part in branch.split("/"))
        ):
            return False
        return True

    @staticmethod
    def _task_step_shape_is_exact(step: Mapping[str, Any]) -> bool:
        allowed = {
            "task", "taskName", "displayName", "name", "inputs",
            "condition", "env", "enabled", "continueOnError",
            "timeoutInMinutes",
        }
        return set(str(key) for key in step).issubset(allowed)

    @staticmethod
    def _is_posix_runner(pool: str) -> bool:
        value = str(pool or "").casefold()
        return any(marker in value for marker in ("ubuntu", "macos"))

    @classmethod
    def _shorthand_step_is_exactly_supported(
        cls, step: Mapping[str, Any], pool: str
    ) -> bool:
        common = {
            "displayName", "name", "condition", "env", "enabled",
            "continueOnError", "timeoutInMinutes",
        }
        if step.get("script") is not None:
            allowed = common | {"script", "workingDirectory", "working-directory"}
            return set(step).issubset(allowed) and cls._is_posix_runner(pool)
        if step.get("bash") is not None:
            allowed = common | {"bash", "workingDirectory", "working-directory"}
            return set(step).issubset(allowed) and cls._is_posix_runner(pool)
        if step.get("powershell") is not None:
            # ADO selects Windows PowerShell 5.1 or PowerShell Core by agent
            # OS, while GitHub's pwsh wrapper also changes error semantics.
            # Require an exact approved workflow step instead of guessing.
            return False
        if step.get("checkout") is not None:
            allowed = common | {
                "checkout", "fetchDepth", "fetchTags", "lfs", "submodules",
                "persistCredentials", "clean", "path", "ref", "token",
            }
            if not set(step).issubset(allowed):
                return False
            checkout = str(step.get("checkout", "")).casefold()
            checkout_options = set(step) - common - {"checkout"}
            if checkout == "none":
                return not checkout_options
            if checkout == "self":
                if any(key in step for key in ("path", "ref", "token")):
                    return False
                if "fetchDepth" not in step or "clean" not in step:
                    return False
                depth = step.get("fetchDepth")
                clean = step.get("clean")
                persist = step.get("persistCredentials", False)
                if (
                    isinstance(depth, bool)
                    or not (
                        isinstance(depth, int) and depth >= 0
                        or isinstance(depth, str) and depth.isdigit()
                    )
                    or not isinstance(clean, bool)
                    or persist is not False
                ):
                    return False
                for key in ("fetchTags", "lfs"):
                    if key in step and not isinstance(step[key], bool):
                        return False
                if "submodules" in step and step["submodules"] not in {
                    True, False, "true", "false", "recursive",
                }:
                    return False
                return True
            # External checkouts require a signed, exact repository mapping;
            # source token/path controls are never copied automatically.
            return not any(key in step for key in ("path", "token"))
        if step.get("publish") is not None:
            return set(step).issubset({
                "publish", "artifact", "displayName", "name",
            })
        if step.get("download") is not None:
            return (
                str(step.get("download", "")).casefold() == "current"
                and set(step).issubset({
                    "download", "artifact", "path", "displayName", "name",
                })
            )
        return False

    @staticmethod
    def condition_is_supported(
        condition: str,
        declared_variables: Optional[set[str]] = None,
        declared_parameters: Optional[set[str]] = None,
    ) -> bool:
        from ado2gh.pipelines.transformer import PipelineTransformer

        expression = str(condition or "").strip()
        if expression.startswith("${{") and expression.endswith("}}"):
            expression = expression[3:-2].strip()

        def supported(value: str) -> bool:
            value = value.strip()
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", value)
            if match:
                if not PipelineTransformer._outer_call_closes_at_end(
                    value, match.end() - 1
                ):
                    return False
                name = match.group(1)
                if name not in _SUPPORTED_CONDITION_FUNCTIONS:
                    return False
                try:
                    args = PipelineTransformer._split_condition_args(
                        value[match.end():-1]
                    )
                except ValueError:
                    return False
                arity_ok = (
                    (name in {
                        "succeeded", "succeededOrFailed", "failed", "always",
                        "canceled", "cancelled",
                    } and len(args) == 0)
                    or (name in {"eq", "ne", "contains", "startsWith", "endsWith"}
                        and len(args) == 2)
                    or (name in {"and", "or", "in", "notIn"} and len(args) >= 2)
                    or (name == "not" and len(args) == 1)
                )
                return arity_ok and all(supported(arg) for arg in args)
            atom = re.fullmatch(
                r"(?:true|false|null|-?[0-9]+(?:\.[0-9]+)?|"
                r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|"
                r"variables(?:\.(?:Build\.(?:SourceBranch|SourceBranchName|Reason)|"
                r"[A-Za-z_][A-Za-z0-9_]*)|"
                r"\[['\"][A-Za-z_][A-Za-z0-9_.-]*['\"]\])|"
                r"parameters(?:\.[A-Za-z_][A-Za-z0-9_]*|"
                r"\[['\"][A-Za-z_][A-Za-z0-9_.-]*['\"]\]))",
                value,
                re.IGNORECASE,
            )
            return atom is not None

        def build_reason_reference(value: str) -> bool:
            return re.fullmatch(
                r"variables(?:\.Build\.Reason|\[(['\"])Build\.Reason\1\])",
                value.strip(),
                re.IGNORECASE,
            ) is not None

        supported_reason_values = {
            "manual", "individualci", "batchedci", "pullrequest", "schedule",
        }

        def build_reason_literal(value: str) -> bool:
            match = re.fullmatch(r"(['\"])(.*?)\1", value.strip())
            return bool(
                match and match.group(2).casefold() in supported_reason_values
            )

        def reason_semantics_supported(value: str) -> bool:
            value = value.strip()
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", value)
            if match and PipelineTransformer._outer_call_closes_at_end(
                value, match.end() - 1
            ):
                name = match.group(1)
                try:
                    args = PipelineTransformer._split_condition_args(
                        value[match.end():-1]
                    )
                except ValueError:
                    return False
                reason_indexes = [
                    index for index, arg in enumerate(args)
                    if build_reason_reference(arg)
                ]
                if reason_indexes:
                    if name in {"eq", "ne"} and len(args) == 2 \
                            and len(reason_indexes) == 1:
                        other = 1 - reason_indexes[0]
                        return build_reason_literal(args[other])
                    if name in {"in", "notIn"} and reason_indexes == [0] \
                            and len(args) >= 2:
                        return all(build_reason_literal(arg) for arg in args[1:])
                    return False
                return all(reason_semantics_supported(arg) for arg in args)
            return not build_reason_reference(value)

        allowed_system_variables = {
            "build.sourcebranch",
            "build.sourcebranchname",
            "build.reason",
        }
        declared_variable_names = {
            str(name).casefold() for name in (declared_variables or set())
        }
        declared_parameter_names = {
            str(name) for name in (declared_parameters or set())
        }

        def reference_name(value: str, root: str) -> Optional[str]:
            escaped_root = re.escape(root)
            bracket = re.fullmatch(
                escaped_root + r"\[(['\"])([A-Za-z_][A-Za-z0-9_.-]*)\1\]",
                value.strip(),
                re.IGNORECASE,
            )
            if bracket:
                return bracket.group(2)
            dotted = re.fullmatch(
                escaped_root + r"\.([A-Za-z_][A-Za-z0-9_]*)",
                value.strip(),
                re.IGNORECASE,
            )
            if dotted:
                return dotted.group(1)
            if root == "variables":
                system = re.fullmatch(
                    r"variables\.(Build\.(?:SourceBranch|SourceBranchName|Reason))",
                    value.strip(),
                    re.IGNORECASE,
                )
                if system:
                    return system.group(1)
            return None

        def reference_semantics_supported(value: str) -> bool:
            value = value.strip()
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", value)
            if match and PipelineTransformer._outer_call_closes_at_end(
                value, match.end() - 1
            ):
                try:
                    args = PipelineTransformer._split_condition_args(
                        value[match.end():-1]
                    )
                except ValueError:
                    return False
                return all(reference_semantics_supported(arg) for arg in args)
            variable = reference_name(value, "variables")
            if variable is not None:
                folded = variable.casefold()
                return folded in allowed_system_variables \
                    or folded in declared_variable_names
            parameter = reference_name(value, "parameters")
            if parameter is not None:
                return parameter in declared_parameter_names
            return not re.match(
                r"^(?:variables|parameters)(?:\.|\[)",
                value,
                re.IGNORECASE,
            )

        return (
            bool(expression)
            and supported(expression)
            and reason_semantics_supported(expression)
            and reference_semantics_supported(expression)
        )

    @staticmethod
    def _safe_parse_yaml(content: str) -> Any:
        if not content:
            return None
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError:
            return None

    @classmethod
    def _raw_yaml_unsupported_constructs(
        cls, document: Mapping[str, Any]
    ) -> list[tuple[tuple[Any, ...], str, Any]]:
        """Find source constructs normalization cannot preserve exactly.

        This grammar runs over the authenticated raw YAML before relying on
        normalized metadata, preventing an extractor omission from becoming a
        deterministic false-pass.
        """
        findings: list[tuple[tuple[Any, ...], str, Any]] = []

        def record(location: tuple[Any, ...], rationale: str, source: Any) -> None:
            findings.append((location, rationale, source))

        top_allowed = {
            "name", "trigger", "pr", "schedules", "variables", "parameters",
            "pool", "stages", "jobs", "steps", "extends", "resources",
            "container", "services",
        }
        for key in sorted(set(document) - top_allowed, key=str):
            record(
                (str(key),),
                f"Top-level ADO YAML key {key!r} is outside the exact source grammar.",
                document[key],
            )

        def check_pool(value: Any, location: tuple[Any, ...]) -> None:
            if isinstance(value, Mapping):
                unknown = set(value) - {"vmImage", "name"}
                if unknown:
                    record(
                        location,
                        "ADO pool demands or auxiliary pool properties are not preserved.",
                        value,
                    )

        check_pool(document.get("pool"), ("pool",))

        variables = document.get("variables")
        if isinstance(variables, Mapping):
            for name, value in variables.items():
                if isinstance(value, (Mapping, list)):
                    record(
                        ("variables", str(name)),
                        "Structured ADO variable definitions are not literal env values.",
                        value,
                    )
        elif isinstance(variables, list):
            for index, variable in enumerate(variables):
                if not isinstance(variable, Mapping):
                    record(
                        ("variables", index),
                        "ADO variable list entries must be exact mappings.",
                        variable,
                    )
                    continue
                allowed = {"group"} if "group" in variable else {"name", "value"}
                if set(variable) - allowed or (
                    "template" in variable
                    or ("group" not in variable and not variable.get("name"))
                ):
                    record(
                        ("variables", index),
                        "Variable templates/read-only metadata are not expanded by the converter.",
                        variable,
                    )
        elif variables is not None:
            record(
                ("variables",),
                "ADO variables must be a mapping or list.",
                variables,
            )

        parameters = document.get("parameters")
        if parameters is not None and not isinstance(parameters, list):
            record(
                ("parameters",),
                "ADO parameters must be an explicit list.",
                parameters,
            )
        elif isinstance(parameters, list):
            for index, parameter in enumerate(parameters):
                if not isinstance(parameter, Mapping) or set(parameter) - {
                    "name", "type", "default", "values",
                }:
                    record(
                        ("parameters", index),
                        "Parameter templates or auxiliary properties are not preserved.",
                        parameter,
                    )

        def check_jobs(jobs: Any, location: tuple[Any, ...]) -> None:
            if jobs is None:
                return
            if not isinstance(jobs, list):
                record(location, "ADO jobs must be a list.", jobs)
                return
            allowed = {
                "job", "deployment", "displayName", "dependsOn", "condition",
                "pool", "steps", "container", "services", "strategy",
                "template", "environment",
            }
            for index, job in enumerate(jobs):
                item_location = location + (index,)
                if not isinstance(job, Mapping):
                    record(item_location, "ADO job must be a mapping.", job)
                    continue
                unknown = set(job) - allowed
                if unknown:
                    record(
                        item_location,
                        "ADO job properties outside the exact grammar are not preserved.",
                        {key: job[key] for key in sorted(unknown)},
                    )
                check_pool(job.get("pool"), item_location + ("pool",))

        stages = document.get("stages")
        if stages is not None:
            if not isinstance(stages, list):
                record(("stages",), "ADO stages must be a list.", stages)
            else:
                stage_allowed = {
                    "stage", "name", "displayName", "dependsOn", "condition",
                    "pool", "jobs", "template",
                }
                for index, stage in enumerate(stages):
                    location = ("stages", index)
                    if not isinstance(stage, Mapping):
                        record(location, "ADO stage must be a mapping.", stage)
                        continue
                    if "template" in stage:
                        record(
                            location + ("template",),
                            "Stage templates must be fetched and expanded before conversion.",
                            stage.get("template"),
                        )
                    unknown = set(stage) - stage_allowed
                    if unknown:
                        record(
                            location,
                            "Stage trigger/lock/variable properties are not preserved.",
                            {key: stage[key] for key in sorted(unknown)},
                        )
                    check_pool(stage.get("pool"), location + ("pool",))
                    check_jobs(stage.get("jobs"), location + ("jobs",))
        check_jobs(document.get("jobs"), ("jobs",))
        return findings

    @staticmethod
    def _source_fingerprint(meta: PipelineMetadata) -> str:
        payload = meta.to_dict()
        # yaml_content is intentionally omitted by PipelineMetadata.to_dict()
        # because StateDB stores normalized metadata. It must still be part of
        # the source fingerprint when inventory has it in memory.
        payload["yaml_content"] = meta.yaml_content or ""
        encoded = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
