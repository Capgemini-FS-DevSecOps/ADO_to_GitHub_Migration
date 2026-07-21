"""Pipeline metadata extraction from raw ADO API responses."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import yaml

from ado2gh.models import (
    PipelineComplexity,
    PipelineEnvironment,
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    PipelineVariable,
)


class PipelineMetadataExtractor:
    """
    Extracts complete normalized PipelineMetadata from raw ADO API responses.
    Handles YAML pipelines, classic build pipelines, and classic release pipelines.
    """

    RULESET_VERSION = "pipeline-extractor-3"

    POOL_MAP = {
        "windows-latest": "windows-latest",
        "ubuntu-latest":  "ubuntu-latest",
        "macos-latest":   "macos-latest",
        "vs2019":         "windows-2019",
        "vs2022":         "windows-2022",
        "ubuntu-22.04":   "ubuntu-22.04",
        "ubuntu-20.04":   "ubuntu-20.04",
        "macos-13":       "macos-13",
    }

    def extract_yaml_pipeline(self, project: str, pipe: dict,
                               definition: dict, build_def: dict,
                               yaml_content: str, runs: list[dict],
                               var_groups: list[dict],
                               service_connections: Optional[list[dict]] = None) -> PipelineMetadata:
        """Extract metadata from a YAML build pipeline."""
        config = definition.get("configuration", {})
        repo   = config.get("repository", {})

        meta = PipelineMetadata(
            pipeline_id   = pipe["id"],
            pipeline_name = pipe["name"],
            pipeline_type = PipelineType.YAML,
            folder        = pipe.get("folder", "\\").strip("\\"),
            project       = project,
            repo_id       = repo.get("id", ""),
            repo_name     = repo.get("name", pipe.get("name", "")),
            repo_type     = repo.get("type", "TfsGit"),
            repo_branch   = repo.get("defaultBranch", "main").replace("refs/heads/", ""),
            source_revision = int(build_def.get("revision", 0) or 0),
            yaml_path     = config.get("path", "azure-pipelines.yml"),
            yaml_content  = yaml_content,
        )

        # Parse triggers from build definition
        if build_def:
            self._extract_build_triggers(meta, build_def)
            self._extract_build_variables(meta, build_def, var_groups)
            self._extract_retention(meta, build_def)

        # Parse stages from YAML content
        if yaml_content:
            self._extract_yaml_structure(meta, yaml_content, var_groups)
            self._extract_task_dependencies(meta, service_connections or [])

        # Run history stats
        self._extract_run_stats(meta, runs)

        # Score complexity
        meta.complexity = self._score_complexity(meta)
        return meta

    def extract_classic_build_pipeline(self, project: str, pipe: dict,
                                       build_def: dict, runs: list[dict],
                                       var_groups: list[dict],
                                       service_connections: Optional[list[dict]] = None) -> PipelineMetadata:
        """Extract metadata from a classic (non-YAML) build pipeline."""
        repo = build_def.get("repository", {})

        meta = PipelineMetadata(
            pipeline_id   = pipe["id"],
            pipeline_name = pipe["name"],
            pipeline_type = PipelineType.CLASSIC,
            folder        = pipe.get("folder", "\\").strip("\\"),
            project       = project,
            repo_id       = repo.get("id", ""),
            repo_name     = repo.get("name", ""),
            repo_type     = repo.get("type", "TfsGit"),
            repo_branch   = repo.get("defaultBranch", "main"),
            source_revision = int(build_def.get("revision", 0) or 0),
        )
        meta.migration_notes.append(
            "Classic build pipeline — auto-transform limited. "
            "Manual GitHub Actions conversion recommended."
        )

        self._extract_build_triggers(meta, build_def)
        self._extract_build_variables(meta, build_def, var_groups)
        self._extract_retention(meta, build_def)

        # Extract phases -> stages (classic)
        for phase in build_def.get("process", {}).get("phases", []):
            phase_steps = [
                self._normalise_classic_task(task)
                for task in phase.get("workflowTasks", phase.get("steps", []))
                if isinstance(task, dict)
            ]
            stage = PipelineStage(
                name        = re.sub(r"[^a-zA-Z0-9_]", "_",
                                     phase.get("name", "build")),
                display_name = phase.get("name", "Build"),
                agent_pool  = self._map_pool(
                    phase.get("target", {}).get("queue", {}).get("name", "ubuntu-latest")
                ),
                condition   = phase.get("condition", ""),
                depends_on  = (
                    phase.get("dependsOn", [])
                    if isinstance(phase.get("dependsOn"), list)
                    else ([phase["dependsOn"]] if phase.get("dependsOn") else [])
                ),
                jobs         = [{
                    "job": phase.get("name", "build"),
                    "displayName": phase.get("name", "Build"),
                    "steps": phase_steps,
                }],
            )
            meta.stages.append(stage)

        self._extract_task_dependencies(meta, service_connections or [])

        self._extract_run_stats(meta, runs)
        meta.complexity = self._score_complexity(meta)
        return meta

    def extract_release_pipeline(self, project: str, rel_def: dict,
                                 service_connections: Optional[list[dict]] = None,
                                 artifact_repositories: Optional[dict[str, dict]] = None
                                 ) -> PipelineMetadata:
        """Extract metadata from a classic release pipeline."""
        meta = PipelineMetadata(
            pipeline_id   = rel_def.get("id", 0),
            pipeline_name = rel_def.get("name", ""),
            pipeline_type = PipelineType.RELEASE,
            folder        = rel_def.get("path", "\\").strip("\\"),
            project       = project,
            source_revision = int(rel_def.get("revision", 0) or 0),
        )
        meta.migration_notes.append(
            "Classic release pipeline — map stages to GitHub Environments "
            "with deployment jobs and required reviewers."
        )

        # Source artifacts -> repo associations. A release artifact's
        # definitionReference.definition.name is a *build pipeline name*, not
        # a repository. Inventory resolves definition ids through the Build
        # Definitions API and passes the repository map explicitly.
        build_artifacts: list[tuple[str, str]] = []
        resolved_repositories: list[dict] = []
        artifact_repositories = artifact_repositories or {}
        for artifact in rel_def.get("artifacts", []):
            if artifact.get("type") == "Build":
                alias = artifact.get("alias", "")
                src   = artifact.get("definitionReference", {})
                definition = src.get("definition", {}) or {}
                definition_id = str(
                    definition.get("id", definition.get("value", "")) or ""
                )
                build_artifacts.append((alias, definition_id))
                repository = artifact_repositories.get(definition_id)
                if repository:
                    resolved_repositories.append(repository)
                meta.migration_notes.append(
                    f"Build artifact '{alias}' -> use needs: + download-artifact action"
                )

        unresolved = [alias for alias, definition_id in build_artifacts
                      if not definition_id or definition_id not in artifact_repositories]
        unique_repositories: dict[str, dict] = {}
        for repository in resolved_repositories:
            identity = str(repository.get("id") or repository.get("name", "")).lower()
            if identity:
                unique_repositories[identity] = repository
        if build_artifacts and not unresolved and len(unique_repositories) == 1:
            repository = next(iter(unique_repositories.values()))
            meta.repo_id = str(repository.get("id", ""))
            meta.repo_name = str(repository.get("name", ""))
            meta.repo_type = str(repository.get("type", "TfsGit"))
            meta.repo_branch = str(repository.get("defaultBranch", "main")).replace(
                "refs/heads/", ""
            )
        elif build_artifacts:
            detail = (
                f"unresolved artifacts: {', '.join(unresolved)}"
                if unresolved else
                f"artifacts resolve to {len(unique_repositories)} repositories"
            )
            meta.migration_notes.append(
                "Release pipeline repository association left unmapped because "
                f"{detail}. A release pipeline is assigned only when every Build "
                "artifact resolves to exactly one repository."
            )
        else:
            meta.migration_notes.append(
                "Release pipeline has no ADO Build artifact from which to resolve a repository."
            )

        # Environments -> stages with deployment metadata
        for env in rel_def.get("environments", []):
            env_name = env.get("name", "")
            approvers = [
                a.get("reviewer", {}).get("displayName", "")
                for step in env.get("preDeployApprovals", {}).get("approvals", [])
                if not step.get("isAutomated", True)
                for a in [step]
            ]
            gh_env = PipelineEnvironment(
                name               = env_name,
                id                 = env.get("id", 0),
                required_approvers = approvers,
                approval_timeout_min = env.get(
                    "preDeployApprovals", {}
                ).get("approvalOptions", {}).get("timeoutInMinutes", 1440),
            )

            # Deployment conditions -> GHA if expressions
            conditions = []
            for condition in env.get("conditions", []):
                if condition.get("conditionType") == 1:
                    conditions.append(f"environment trigger: {condition.get('value', '')}")

            stage = PipelineStage(
                name          = re.sub(r"[^a-zA-Z0-9_]", "_", env_name),
                display_name  = env_name,
                environment   = gh_env,
                is_deployment = True,
                condition     = env.get("condition", ""),
                jobs          = self._release_environment_jobs(env),
            )
            meta.stages.append(stage)
            meta.environments.append(gh_env)

        # Variables
        for name, val in rel_def.get("variables", {}).items():
            meta.variables.append(PipelineVariable(
                name      = name,
                value     = val.get("value", ""),
                is_secret = val.get("isSecret", False),
            ))

        self._extract_task_dependencies(meta, service_connections or [])
        meta.complexity = self._score_complexity(meta)
        return meta

    # -- Helpers ----------------------------------------------------------------

    def _extract_build_triggers(self, meta: PipelineMetadata, build_def: dict):
        """Extract CI, PR, and schedule triggers from a build definition."""
        # CI triggers
        for trigger in build_def.get("triggers", []):
            try:
                t_type = int(trigger.get("triggerType", 0))
            except (TypeError, ValueError):
                t_type = 0
            raw_branches = trigger.get("branchFilters", []) or []
            branches = [
                self._normalise_branch(str(branch).lstrip("+"))
                for branch in raw_branches
                if not str(branch).startswith("-")
            ]
            excluded_branches = [
                self._normalise_branch(str(branch).lstrip("-"))
                for branch in raw_branches
                if str(branch).startswith("-")
            ]
            raw_paths = trigger.get("pathFilters", []) or []
            included_paths = [
                self._normalise_path_filter(str(path).lstrip("+"))
                for path in raw_paths
                if not str(path).startswith("-")
            ]
            excluded_paths = [
                self._normalise_path_filter(str(path).lstrip("-"))
                for path in raw_paths
                if str(path).startswith("-")
            ]
            if t_type in {2, 4}:  # ContinuousIntegration / BatchedCI
                meta.trigger_branches.extend(branches or ["**"])
                meta.trigger_branch_excludes.extend(excluded_branches)
                meta.trigger_path_includes.extend(included_paths)
                meta.trigger_path_excludes.extend(excluded_paths)
                meta.trigger_batch = (
                    meta.trigger_batch
                    or t_type == 4
                    or self._coerce_bool(
                        trigger.get("batchChanges", trigger.get("batch", False)),
                        default=False,
                    )
                )
                self._record_trigger_filter_notes(
                    meta, "Classic CI", excluded_branches,
                    included_paths, excluded_paths,
                )
                if meta.trigger_batch:
                    meta.migration_notes.append(
                        "Classic CI batched changes require trigger parity review."
                    )
            elif t_type == 64:  # PullRequest
                meta.trigger_pr_branches.extend(branches or ["**"])
                meta.trigger_pr_branch_excludes.extend(excluded_branches)
                meta.trigger_pr_path_includes.extend(included_paths)
                meta.trigger_pr_path_excludes.extend(excluded_paths)
                meta.trigger_pr_auto_cancel = self._coerce_bool(
                    trigger.get("autoCancel", True), default=True,
                )
                meta.trigger_pr_drafts = self._coerce_bool(
                    trigger.get("drafts", True), default=True,
                )
                self._record_trigger_filter_notes(
                    meta, "Classic PR", excluded_branches,
                    included_paths, excluded_paths,
                )
                if meta.trigger_pr_auto_cancel:
                    meta.migration_notes.append(
                        "Classic PR auto-cancel requires trigger parity review."
                    )
                if meta.trigger_pr_drafts is False:
                    meta.migration_notes.append(
                        "Classic PR draft exclusion requires trigger parity review."
                    )
        # Schedules
        for sched in build_def.get("schedules", []):
            raw_branch_filters = sched.get("branchFilters", []) or []
            meta.trigger_schedules.append({
                "daysToRun":      sched.get("daysToBuild", sched.get("daysToRun", 0)),
                "branch_filters": [
                    self._normalise_branch(str(branch).lstrip("+"))
                    for branch in raw_branch_filters
                    if not str(branch).startswith("-")
                ],
                "excluded_branches": [
                    self._normalise_branch(str(branch).lstrip("-"))
                    for branch in raw_branch_filters
                    if str(branch).startswith("-")
                ],
                "always":         not sched.get("scheduleOnlyWithChanges", False),
                "hour":           sched.get("startHours", sched.get("hour", 0)),
                "minute":         sched.get("startMinutes", sched.get("minute", 0)),
            })

    def _extract_build_variables(self, meta: PipelineMetadata,
                                 build_def: dict, var_groups: list[dict]):
        """Extract inline variables and variable group references."""
        vg_map = {vg["id"]: vg for vg in var_groups}
        for name, val in build_def.get("variables", {}).items():
            meta.variables.append(PipelineVariable(
                name      = name,
                value     = val.get("value", ""),
                is_secret = val.get("isSecret", False),
            ))
        for vg_ref in build_def.get("variableGroups", []):
            vg_id = vg_ref if isinstance(vg_ref, int) else vg_ref.get("id", 0)
            vg    = vg_map.get(vg_id, {})
            meta.variable_groups.append({
                "id":        vg_id,
                "name":      vg.get("name", f"group-{vg_id}"),
                "type":      vg.get("type", "Vsts"),
                "variables": list(vg.get("variables", {}).keys()),
            })

    def _extract_retention(self, meta: PipelineMetadata, build_def: dict):
        """Extract retention rules from a build definition."""
        rules = build_def.get("retentionRules", [{}])
        if rules:
            meta.retention_days = rules[0].get("daysToKeep", 30)

    def _extract_yaml_structure(self, meta: PipelineMetadata,
                                yaml_content: str, var_groups: list[dict]):
        """Parse YAML content for stages, variables, pool overrides."""
        try:
            doc = yaml.safe_load(yaml_content) or {}
        except Exception:
            meta.migration_notes.append("Could not parse YAML — manual review required.")
            return
        if not isinstance(doc, dict):
            meta.migration_notes.append(
                "Pipeline YAML root is not a mapping — manual review required."
            )
            return

        self._extract_yaml_triggers(meta, doc)

        # Top-level variables — supports both list-form and mapping-form.
        raw_vars = doc.get("variables", [])
        if isinstance(raw_vars, dict):
            # mapping form:  variables: { name: value, ... }
            for name, value in raw_vars.items():
                meta.variables.append(PipelineVariable(
                    name  = str(name),
                    value = str(value),
                ))
        elif isinstance(raw_vars, list):
            for var in raw_vars:
                if not isinstance(var, dict):
                    continue
                if "group" in var:
                    vg_name = var["group"]
                    vg = next((v for v in var_groups
                               if v.get("name") == vg_name), {})
                    meta.variable_groups.append({
                        "id":        vg.get("id", 0),
                        "name":      vg_name,
                        "type":      vg.get("type", "Vsts"),
                        "variables": list(vg.get("variables", {}).keys()),
                    })
                else:
                    meta.variables.append(PipelineVariable(
                        name  = var.get("name", ""),
                        value = str(var.get("value", "")),
                    ))

        # Top-level parameters (template parameters in ADO YAML).
        for p in doc.get("parameters", []) or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            entry: dict = {
                "name":    p["name"],
                "type":    p.get("type", "string"),
                "default": p.get("default"),
            }
            if isinstance(p.get("values"), list):
                entry["values"] = list(p["values"])
            meta.parameters.append(entry)

        # Stages
        raw_stages = doc.get("stages", [])
        if raw_stages:
            for s in raw_stages:
                if not isinstance(s, dict):
                    continue
                env_name = None
                is_deploy = False
                for deploy_job in s.get("jobs", []) or []:
                    if not isinstance(deploy_job, dict) or "deployment" not in deploy_job:
                        continue
                    raw_environment = deploy_job.get("environment", "")
                    env_name = (
                        raw_environment.get("name", "")
                        if isinstance(raw_environment, dict)
                        else str(raw_environment)
                    )
                    is_deploy = True
                    if env_name:
                        break

                gh_env = None
                if env_name:
                    gh_env = PipelineEnvironment(name=env_name)
                    meta.environments.append(gh_env)

                pool = s.get("pool", doc.get("pool", {}))
                runner = self._map_pool(
                    pool.get("vmImage", pool.get("name", "ubuntu-latest"))
                    if isinstance(pool, dict) else str(pool or "ubuntu-latest")
                )

                stage = PipelineStage(
                    name         = re.sub(r"[^a-zA-Z0-9_]", "_",
                                          str(s.get("stage") or s.get("name") or "stage")),
                    display_name = str(s.get("displayName") or ""),
                    depends_on   = (s.get("dependsOn", [])
                                    if isinstance(s.get("dependsOn"), list)
                                    else ([s["dependsOn"]] if s.get("dependsOn") else [])),
                    condition    = str(s.get("condition") or ""),
                    environment  = gh_env,
                    is_deployment = is_deploy,
                    agent_pool   = runner,
                    jobs         = s.get("jobs", []),
                )
                meta.stages.append(stage)
        elif doc.get("jobs"):
            # Single-stage, multiple jobs
            meta.stages.append(PipelineStage(
                name    = "build",
                jobs    = doc.get("jobs", []),
                agent_pool = self._pool_from_yaml(doc.get("pool")),
            ))
        elif doc.get("steps"):
            # Bare-steps pipeline — wrap them in a synthetic single job so
            # the transformer can map them just like a regular jobs block.
            meta.stages.append(PipelineStage(
                name = "build",
                jobs = [{"job": "build", "steps": doc.get("steps", [])}],
                agent_pool = self._pool_from_yaml(doc.get("pool")),
            ))
        else:
            # Implicit single stage
            meta.stages.append(PipelineStage(name="build", jobs=[]))

        # Agent pool at top level
        pool = doc.get("pool", {})
        if isinstance(pool, dict):
            meta.agent_pools.append(
                self._map_pool(pool.get("vmImage", pool.get("name", "ubuntu-latest")))
            )

    def _extract_yaml_triggers(self, meta: PipelineMetadata, doc: dict) -> None:
        """Normalize YAML-native CI/PR/schedule triggers."""

        def branch_rules(raw: object) -> tuple[list[str], list[str]]:
            if raw in (None, "none", False):
                return [], []
            if isinstance(raw, str):
                return [self._normalise_branch(raw)], []
            if isinstance(raw, list):
                return [self._normalise_branch(str(item)) for item in raw], []
            if not isinstance(raw, dict):
                return [], []
            branches = raw.get("branches", raw)
            if isinstance(branches, list):
                return [self._normalise_branch(str(item)) for item in branches], []
            if not isinstance(branches, dict):
                return [], []
            include = branches.get("include", []) or []
            exclude = branches.get("exclude", []) or []
            if isinstance(include, str):
                include = [include]
            if isinstance(exclude, str):
                exclude = [exclude]
            return (
                [self._normalise_branch(str(item)) for item in include],
                [self._normalise_branch(str(item)) for item in exclude],
            )

        def path_rules(raw: object) -> tuple[list[str], list[str]]:
            if not isinstance(raw, dict):
                return [], []
            paths = raw.get("paths", {})
            if isinstance(paths, str):
                return [self._normalise_path_filter(paths)], []
            if isinstance(paths, list):
                return [
                    self._normalise_path_filter(str(item)) for item in paths
                ], []
            if not isinstance(paths, dict):
                return [], []
            include = paths.get("include", []) or []
            exclude = paths.get("exclude", []) or []
            if isinstance(include, str):
                include = [include]
            if isinstance(exclude, str):
                exclude = [exclude]
            return (
                [self._normalise_path_filter(str(item)) for item in include],
                [self._normalise_path_filter(str(item)) for item in exclude],
            )

        trigger = doc.get("trigger")
        includes, excludes = branch_rules(trigger)
        if trigger not in (None, "none", False):
            meta.trigger_branches.extend(includes or ["**"])
            meta.trigger_branch_excludes.extend(excludes)
            path_includes, path_excludes = path_rules(trigger)
            meta.trigger_path_includes.extend(path_includes)
            meta.trigger_path_excludes.extend(path_excludes)
            if isinstance(trigger, dict):
                meta.trigger_batch = meta.trigger_batch or self._coerce_bool(
                    trigger.get("batch", False), default=False,
                )
            self._record_trigger_filter_notes(
                meta, "CI", excludes, path_includes, path_excludes,
            )
            if meta.trigger_batch:
                meta.migration_notes.append(
                    "CI trigger batch=true requires trigger parity review."
                )

        pr = doc.get("pr")
        includes, excludes = branch_rules(pr)
        if pr not in (None, "none", False):
            meta.trigger_pr_branches.extend(includes or ["**"])
            meta.trigger_pr_branch_excludes.extend(excludes)
            path_includes, path_excludes = path_rules(pr)
            meta.trigger_pr_path_includes.extend(path_includes)
            meta.trigger_pr_path_excludes.extend(path_excludes)
            meta.trigger_pr_auto_cancel = self._coerce_bool(
                pr.get("autoCancel", True) if isinstance(pr, dict) else True,
                default=True,
            )
            meta.trigger_pr_drafts = self._coerce_bool(
                pr.get("drafts", True) if isinstance(pr, dict) else True,
                default=True,
            )
            self._record_trigger_filter_notes(
                meta, "PR", excludes, path_includes, path_excludes,
            )
            if meta.trigger_pr_auto_cancel:
                meta.migration_notes.append(
                    "PR trigger autoCancel=true requires trigger parity review."
                )
            if meta.trigger_pr_drafts is False:
                meta.migration_notes.append(
                    "PR trigger drafts=false requires trigger parity review."
                )

        for schedule in doc.get("schedules", []) or []:
            if not isinstance(schedule, dict) or not schedule.get("cron"):
                continue
            branches, excluded = branch_rules(schedule.get("branches", {}))
            meta.trigger_schedules.append({
                "cron": str(schedule["cron"]).strip(),
                "branch_filters": branches,
                "excluded_branches": excluded,
                "always": bool(schedule.get("always", False)),
                "display_name": str(schedule.get("displayName", "")),
            })

    def _extract_task_dependencies(
        self,
        meta: PipelineMetadata,
        service_connections: list[dict],
    ) -> None:
        """Discover unsupported tasks and referenced service connections."""
        from ado2gh.pipelines.transformer import ADO_TASK_MAP

        known_connections: dict[str, dict] = {}
        for connection in service_connections:
            for value in (connection.get("id"), connection.get("name")):
                if value:
                    known_connections[str(value).lower()] = connection
        connection_key = re.compile(
            r"(?i)(connectedservice|serviceconnection|subscription|endpoint|azure_rm)"
        )
        unsupported: set[str] = set(meta.unsupported_tasks)
        found_connections: dict[str, dict] = {
            str(item.get("id", item.get("name", ""))): item
            for item in meta.service_connections
        }
        for stage in meta.stages:
            for raw_job in stage.jobs:
                if not isinstance(raw_job, dict):
                    continue
                steps = raw_job.get("steps", [raw_job])
                if not isinstance(steps, list):
                    continue
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    task_name = str(step.get("task", step.get("taskName", "")))
                    if task_name and task_name not in ADO_TASK_MAP:
                        unsupported.add(task_name)
                    inputs = step.get("inputs", {})
                    if not isinstance(inputs, dict):
                        continue
                    for key, raw_value in inputs.items():
                        if not connection_key.search(str(key)) or not raw_value:
                            continue
                        value = str(raw_value)
                        if value.startswith("$(") or value.startswith("${{"):
                            continue
                        match = known_connections.get(value.lower())
                        item = (
                            {
                                "id": match.get("id", ""),
                                "name": match.get("name", value),
                                "type": match.get("type", "unknown"),
                            }
                            if match else {"id": "", "name": value, "type": "unknown"}
                        )
                        found_connections[str(item.get("id") or item["name"])] = item
        meta.unsupported_tasks = sorted(unsupported)
        meta.service_connections = list(found_connections.values())

    @classmethod
    def _normalise_classic_task(cls, raw: dict) -> dict:
        result: dict = {
            "displayName": raw.get("displayName", raw.get("name", "")),
            "inputs": dict(raw.get("inputs", {})),
            "enabled": raw.get("enabled", True),
            "condition": raw.get("condition", ""),
        }
        task_ref = raw.get("task", {})
        task_name = raw.get("taskName", "")
        version = ""
        task_id = raw.get("taskId", "")
        if isinstance(task_ref, str):
            task_name = task_ref
        elif isinstance(task_ref, dict):
            task_name = task_name or task_ref.get("name", "")
            task_id = task_id or task_ref.get("id", "")
            version = task_ref.get("versionSpec", task_ref.get("version", ""))
        raw_version = raw.get("version", version)
        if isinstance(raw_version, dict):
            version = str(raw_version.get("major", ""))
        else:
            version = str(raw_version or version)
        version = version.split(".", 1)[0].rstrip("*")
        if task_name and "@" not in str(task_name) and version:
            task_name = f"{task_name}@{version}"
        if not task_name:
            task_name = f"ClassicTask:{task_id or 'unknown'}"
            if version:
                task_name += f"@{version}"
        result["task"] = str(task_name)
        if raw.get("environment"):
            result["env"] = dict(raw["environment"])
        return result

    @classmethod
    def _release_environment_jobs(cls, environment: dict) -> list[dict]:
        jobs: list[dict] = []
        for index, phase in enumerate(environment.get("deployPhases", []) or []):
            if not isinstance(phase, dict):
                continue
            tasks = [
                cls._normalise_classic_task(task)
                for task in phase.get("workflowTasks", []) or []
                if isinstance(task, dict)
            ]
            phase_name = phase.get("name", f"deployment_{index + 1}")
            deployment_input = phase.get("deploymentInput", {}) or {}
            queue = deployment_input.get("queue", {}) or {}
            jobs.append({
                "deployment": phase_name,
                "displayName": phase_name,
                "environment": environment.get("name", ""),
                "pool": {"name": queue.get("name", "ubuntu-latest")},
                "steps": tasks,
            })
        return jobs

    def _pool_from_yaml(self, pool: object) -> str:
        if isinstance(pool, dict):
            return self._map_pool(str(pool.get("vmImage", pool.get("name", "ubuntu-latest"))))
        return self._map_pool(str(pool or "ubuntu-latest"))

    @staticmethod
    def _normalise_branch(branch: str) -> str:
        value = str(branch or "").strip()
        return value.replace("refs/heads/", "", 1) if value.startswith("refs/heads/") else value

    @staticmethod
    def _normalise_path_filter(path: str) -> str:
        return str(path or "").strip().replace("\\", "/")

    @staticmethod
    def _coerce_bool(value: object, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
        return default

    @staticmethod
    def _record_trigger_filter_notes(
        meta: PipelineMetadata,
        label: str,
        excluded_branches: list[str],
        included_paths: list[str],
        excluded_paths: list[str],
    ) -> None:
        if excluded_branches:
            meta.migration_notes.append(
                f"{label} branch excludes require trigger parity review: "
                + ", ".join(excluded_branches)
            )
        if included_paths:
            meta.migration_notes.append(
                f"{label} path includes require trigger parity review: "
                + ", ".join(included_paths)
            )
        if excluded_paths:
            meta.migration_notes.append(
                f"{label} path excludes require trigger parity review: "
                + ", ".join(excluded_paths)
            )

    def _extract_run_stats(self, meta: PipelineMetadata, runs: list[dict]):
        """Compute run history statistics from recent pipeline runs."""
        if not runs:
            return
        last = runs[0]
        meta.last_run_id     = last.get("id")
        meta.last_run_result = last.get("result", "")
        fin = last.get("finishedDate", "")
        meta.last_run_date   = fin[:10] if fin else ""
        durations: list[float] = []
        for r in runs:
            start = r.get("createdDate", "")
            end   = r.get("finishedDate", "")
            if start and end:
                try:
                    s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    durations.append((e - s).total_seconds() / 60)
                except Exception:
                    pass
        meta.avg_duration_min = round(sum(durations) / len(durations), 1) if durations else 0.0
        meta.total_runs_30d   = len(runs)

    def _map_pool(self, pool_name: str) -> str:
        """Map an ADO agent pool name to a GitHub Actions runner label."""
        raw = str(pool_name or "ubuntu-latest").strip()
        lower = raw.lower()
        exact = {key.lower(): value for key, value in self.POOL_MAP.items()}
        if lower in exact:
            return exact[lower]
        if "ubuntu" in lower:
            return "ubuntu-latest"
        if "windows" in lower or "vs20" in lower:
            return "windows-latest"
        if "macos" in lower or lower.startswith("mac"):
            return "macos-latest"
        # Preserve unknown/self-hosted pool names so the planner can require an
        # explicit enterprise runner mapping instead of silently using Ubuntu.
        return raw

    def _score_complexity(self, meta: PipelineMetadata) -> PipelineComplexity:
        """Score pipeline complexity based on structural signals."""
        score = 0
        score += len(meta.stages) * 2
        score += len(meta.environments) * 3
        score += len(meta.variable_groups) * 2
        score += 5 if meta.pipeline_type == PipelineType.RELEASE else 0
        score += 3 if meta.pipeline_type == PipelineType.CLASSIC else 0
        score += sum(2 for e in meta.environments if e.required_approvers)
        score += 1 if meta.trigger_schedules else 0
        score += len(meta.service_connections)

        if score <= 4:
            return PipelineComplexity.SIMPLE
        elif score <= 14:
            return PipelineComplexity.MEDIUM
        else:
            return PipelineComplexity.COMPLEX
