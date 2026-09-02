"""Job graph builder — preserves multi-job ADO stages with needs edges."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

import yaml

from ado2gh.models import PipelineMetadata, PipelineStage
from ado2gh.pipelines.transform.expressions import map_condition
from ado2gh.pipelines.transform.task_registry import _POOL_RUNNER_MAP


def resolve_runner(pool_name: str) -> str:
    if not pool_name:
        return "ubuntu-latest"
    lower = pool_name.lower()
    for fragment, runner in _POOL_RUNNER_MAP.items():
        if fragment in lower:
            return runner
    return "ubuntu-latest"


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name).lower()


def build_multi_stage_jobs(
    meta: PipelineMetadata,
    warnings: list[str],
    unsupported: list[str],
    map_step: Callable[[dict, list, list], Optional[dict]],
) -> dict[str, Any]:
    """Build GHA jobs preserving per-ADO-job graph within each stage."""
    jobs: dict[str, Any] = {}
    prior_stage_job_ids: list[str] = []

    for stage in meta.stages:
        stage_job_ids = _jobs_for_stage(
            stage, prior_stage_job_ids, warnings, unsupported, map_step,
        )
        jobs.update(stage_job_ids)
        prior_stage_job_ids = list(stage_job_ids.keys())

    return jobs


def _jobs_for_stage(
    stage: PipelineStage,
    prior_stage_job_ids: list[str],
    warnings: list[str],
    unsupported: list[str],
    map_step: Callable[[dict, list, list], Optional[dict]],
) -> dict[str, Any]:
    jobs: dict[str, Any] = {}
    raw_jobs = stage.jobs or []

    if not raw_jobs:
        job_id = _slug(stage.name or "build")
        jobs[job_id] = _build_single_job(stage, [], warnings, unsupported, map_step)
        if prior_stage_job_ids:
            jobs[job_id]["needs"] = list(prior_stage_job_ids)
        return jobs

    # Multiple ADO jobs — one GHA job each with dependsOn -> needs.
    ado_job_id_map: dict[str, str] = {}

    for idx, raw_job in enumerate(raw_jobs):
        if not isinstance(raw_job, dict):
            continue
        job_body = raw_job
        ado_job_name = (
            job_body.get("job")
            or job_body.get("displayName")
            or job_body.get("name")
            or f"job{idx}"
        )
        gha_job_id = _slug(f"{stage.name}_{ado_job_name}")
        ado_job_id_map[str(ado_job_name)] = gha_job_id
        ado_job_id_map[_slug(str(ado_job_name))] = gha_job_id

        pool = job_body.get("pool", stage.agent_pool or "ubuntu-latest")
        if isinstance(pool, dict):
            runner = resolve_runner(pool.get("vmImage", "ubuntu-latest"))
        else:
            runner = resolve_runner(str(pool) if pool else stage.agent_pool)

        steps: list[dict] = [{"uses": "actions/checkout@v4"}]
        for step in job_body.get("steps", []):
            mapped = map_step(step, warnings, unsupported)
            if mapped:
                steps.append(mapped)

        job: dict[str, Any] = {
            "name": str(ado_job_name),
            "runs-on": runner,
            "steps": steps,
        }

        depends = job_body.get("dependsOn", [])
        if isinstance(depends, str):
            depends = [depends]
        needs: list[str] = []
        for dep in depends or []:
            dep_key = ado_job_id_map.get(str(dep)) or ado_job_id_map.get(_slug(str(dep)))
            if dep_key:
                needs.append(dep_key)
        if not needs and prior_stage_job_ids:
            needs = list(prior_stage_job_ids)
        if needs:
            job["needs"] = needs

        if stage.condition:
            job["if"] = map_condition(stage.condition)
        if stage.environment and stage.is_deployment:
            job["environment"] = stage.environment.name

        jobs[gha_job_id] = job

    return jobs


def _build_single_job(
    stage: PipelineStage,
    prior_needs: list[str],
    warnings: list[str],
    unsupported: list[str],
    map_step: Callable[[dict, list, list], Optional[dict]],
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "name": stage.display_name or stage.name,
        "runs-on": resolve_runner(stage.agent_pool),
    }
    if prior_needs:
        job["needs"] = list(prior_needs)
    if stage.depends_on:
        job["needs"] = [_slug(d) for d in stage.depends_on]
    if stage.condition:
        job["if"] = map_condition(stage.condition)
    if stage.environment and stage.is_deployment:
        job["environment"] = stage.environment.name

    steps: list[dict] = [{"uses": "actions/checkout@v4"}]
    if stage.variables:
        stage_env = {}
        for v in stage.variables:
            if v.is_secret:
                stage_env[v.name] = f"${{{{ secrets.{v.name} }}}}"
            else:
                stage_env[v.name] = v.value
        job["env"] = stage_env

    for job_dict in stage.jobs or []:
        for step in job_dict.get("steps", [job_dict]):
            mapped = map_step(step, warnings, unsupported)
            if mapped:
                steps.append(mapped)

    job["steps"] = steps
    return job


def build_jobs_from_yaml(
    meta: PipelineMetadata,
    warnings: list[str],
    unsupported: list[str],
    map_step: Callable[[dict, list, list], Optional[dict]],
    default_jobs_fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Parse jobs/stages from embedded YAML — one GHA job per ADO job."""
    jobs: dict[str, Any] = {}
    try:
        parsed = yaml.safe_load(meta.yaml_content)
    except Exception:
        warnings.append(
            "Failed to parse embedded YAML content – falling back to "
            "default job scaffold."
        )
        return default_jobs_fn()

    if not isinstance(parsed, dict):
        return default_jobs_fn()

    raw_stages = parsed.get("stages", [])
    if raw_stages:
        prior: list[str] = []
        for raw_stage in raw_stages:
            stage_body = raw_stage.get("stage", raw_stage)
            if isinstance(stage_body, str):
                stage_name = stage_body
                stage_body = raw_stage if isinstance(raw_stage, dict) else {"stage": stage_name}
            else:
                stage_name = stage_body.get("stage", stage_body.get("displayName", "build"))
            raw_jobs = stage_body.get("jobs", [])
            stage = PipelineStage(
                name=_slug(str(stage_name)),
                display_name=str(stage_name),
                agent_pool=(
                    stage_body.get("pool", {}).get("vmImage", "ubuntu-latest")
                    if isinstance(stage_body.get("pool"), dict)
                    else "ubuntu-latest"
                ),
                jobs=raw_jobs,
            )
            stage_jobs = _jobs_for_stage(stage, prior, warnings, unsupported, map_step)
            jobs.update(stage_jobs)
            prior = list(stage_jobs.keys())
        return jobs

    raw_jobs = parsed.get("jobs", [])
    if raw_jobs:
        stage = PipelineStage(name="build", jobs=raw_jobs)
        return _jobs_for_stage(stage, [], warnings, unsupported, map_step)

    raw_steps = parsed.get("steps", [])
    steps = [{"uses": "actions/checkout@v4"}]
    for step in raw_steps:
        mapped = map_step(step, warnings, unsupported)
        if mapped:
            steps.append(mapped)
    pool = parsed.get("pool", {})
    runner = resolve_runner(
        pool.get("vmImage", "ubuntu-latest") if isinstance(pool, dict) else str(pool)
    )
    jobs["build"] = {"name": "Build", "runs-on": runner, "steps": steps}
    return jobs


def build_default_jobs(
    meta: PipelineMetadata,
    warnings: list[str],
) -> dict[str, Any]:
    runner = resolve_runner(meta.agent_pools[0] if meta.agent_pools else "ubuntu-latest")
    warnings.append("No stages or steps were found – a placeholder job was generated.")
    return {
        "build": {
            "name": "Build",
            "runs-on": runner,
            "steps": [
                {"uses": "actions/checkout@v4"},
                {
                    "name": "TODO: add build steps",
                    "run": (
                        'echo "This workflow was auto-generated from ADO pipeline '
                        f"'{meta.pipeline_name}'. Add your build steps here.\""
                    ),
                },
            ],
        }
    }
