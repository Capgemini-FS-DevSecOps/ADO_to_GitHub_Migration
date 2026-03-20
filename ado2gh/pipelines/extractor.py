"""Pipeline metadata extraction from raw ADO API responses."""
from __future__ import annotations

import re
from datetime import datetime

import yaml

from ado2gh.logging_config import log
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
                               var_groups: list[dict]) -> PipelineMetadata:
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

        # Run history stats
        self._extract_run_stats(meta, runs)

        # Score complexity
        meta.complexity = self._score_complexity(meta)
        return meta

    def extract_classic_build_pipeline(self, project: str, pipe: dict,
                                       build_def: dict, runs: list[dict],
                                       var_groups: list[dict]) -> PipelineMetadata:
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
            stage = PipelineStage(
                name        = re.sub(r"[^a-zA-Z0-9_]", "_",
                                     phase.get("name", "build")),
                display_name = phase.get("name", "Build"),
                agent_pool  = self._map_pool(
                    phase.get("target", {}).get("queue", {}).get("name", "ubuntu-latest")
                ),
            )
            meta.stages.append(stage)

        self._extract_run_stats(meta, runs)
        meta.complexity = self._score_complexity(meta)
        return meta

    def extract_release_pipeline(self, project: str, rel_def: dict) -> PipelineMetadata:
        """Extract metadata from a classic release pipeline."""
        meta = PipelineMetadata(
            pipeline_id   = rel_def.get("id", 0),
            pipeline_name = rel_def.get("name", ""),
            pipeline_type = PipelineType.RELEASE,
            folder        = rel_def.get("path", "\\").strip("\\"),
            project       = project,
        )
        meta.migration_notes.append(
            "Classic release pipeline — map stages to GitHub Environments "
            "with deployment jobs and required reviewers."
        )

        # Source artifacts -> repo associations
        for artifact in rel_def.get("artifacts", []):
            if artifact.get("type") == "Build":
                alias = artifact.get("alias", "")
                src   = artifact.get("definitionReference", {})
                meta.repo_name = src.get("definition", {}).get("name", "")
                meta.migration_notes.append(
                    f"Build artifact '{alias}' -> use needs: + download-artifact action"
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

        meta.complexity = self._score_complexity(meta)
        return meta

    # -- Helpers ----------------------------------------------------------------

    def _extract_build_triggers(self, meta: PipelineMetadata, build_def: dict):
        """Extract CI, PR, and schedule triggers from a build definition."""
        # CI triggers
        for trigger in build_def.get("triggers", []):
            t_type = trigger.get("triggerType", 0)
            branches = [
                b.lstrip("+") for b in trigger.get("branchFilters", [])
                if not b.startswith("-")
            ]
            if t_type == 2:    # ContinuousIntegration
                meta.trigger_branches.extend(branches)
            elif t_type == 64:  # PullRequest
                meta.trigger_pr_branches.extend(branches)
        # Schedules
        for sched in build_def.get("schedules", []):
            meta.trigger_schedules.append({
                "cron":           sched.get("daysToBuild", ""),
                "branch_filters": sched.get("branchFilters", []),
                "always":         sched.get("scheduleOnlyWithChanges", False),
                "start_hours":    sched.get("startHours", 0),
                "start_minutes":  sched.get("startMinutes", 0),
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

        # Top-level variables
        for var in doc.get("variables", []):
            if isinstance(var, dict):
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

        # Stages
        raw_stages = doc.get("stages", [])
        if raw_stages:
            for s in raw_stages:
                if not isinstance(s, dict):
                    continue
                env_name   = None
                is_deploy  = "deployment" in str(s)
                deploy_job = s.get("jobs", [{}])[0] if s.get("jobs") else {}
                if isinstance(deploy_job, dict) and "deployment" in str(deploy_job):
                    env_name = (deploy_job.get("environment", {}).get("name", "")
                                if isinstance(deploy_job.get("environment"), dict)
                                else str(deploy_job.get("environment", "")))
                    is_deploy = True

                gh_env = None
                if env_name:
                    gh_env = PipelineEnvironment(name=env_name)
                    meta.environments.append(gh_env)

                pool = s.get("pool", {})
                runner = self._map_pool(
                    pool.get("vmImage", "ubuntu-latest") if isinstance(pool, dict)
                    else "ubuntu-latest"
                )

                stage = PipelineStage(
                    name         = re.sub(r"[^a-zA-Z0-9_]", "_",
                                          s.get("stage", s.get("name", "stage"))),
                    display_name = s.get("displayName", ""),
                    depends_on   = (s.get("dependsOn", [])
                                    if isinstance(s.get("dependsOn"), list)
                                    else ([s["dependsOn"]] if s.get("dependsOn") else [])),
                    condition    = s.get("condition", ""),
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
            ))
        else:
            # Implicit single stage
            meta.stages.append(PipelineStage(name="build", jobs=[]))

        # Agent pool at top level
        pool = doc.get("pool", {})
        if isinstance(pool, dict):
            meta.agent_pools.append(
                self._map_pool(pool.get("vmImage", "ubuntu-latest"))
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
        return self.POOL_MAP.get(pool_name, "ubuntu-latest")

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
