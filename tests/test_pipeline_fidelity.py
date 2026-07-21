from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ado2gh.models import PipelineMetadata, PipelineStage, PipelineType
from ado2gh.pipelines.extractor import PipelineMetadataExtractor
from ado2gh.pipelines.pev_types import ConversionMode, ConversionPlan
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.transformer import PipelineTransformer
from ado2gh.pipelines.validator import PipelineWorkflowValidator


def _meta(
    step: dict | None = None,
    *,
    schedules: list[dict] | None = None,
) -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=91,
        pipeline_name="Fidelity Build",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="api",
        repo_branch="main",
        trigger_schedules=list(schedules or []),
        stages=[PipelineStage(
            name="build",
            jobs=[{
                "job": "build",
                "steps": [step or {"bash": "make test"}],
            }],
        )],
    )


def _workflow(path: Path) -> dict:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _triggers(workflow: dict) -> dict:
    # PyYAML 1.1 can interpret an unquoted `on` key as boolean true.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _docker_step(workflow: dict) -> dict:
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("docker/build-push-action@"):
                return step
    raise AssertionError("generated workflow has no Docker build/push step")


@pytest.mark.parametrize("explicit_command", [True, False])
def test_docker_build_and_push_preserves_push_and_requires_registry_review(
    tmp_path, explicit_command
):
    inputs = {
        "buildContext": ".",
        "Dockerfile": "Dockerfile",
        "containerRegistry": "production-acr",
        "repository": "payments/api",
        "tags": "latest",
    }
    if explicit_command:
        inputs["command"] = "buildAndPush"
    meta = _meta({"task": "Docker@2", "inputs": inputs})

    result = PipelineTransformer().transform(meta, tmp_path)
    docker = _docker_step(_workflow(result["workflow_file"]))
    ambiguities = result["plan"]["ambiguities"]

    assert str(docker["with"]["push"]).casefold() == "true"
    assert result["validation"]["valid"] is True
    assert result["production_ready"] is False
    assert any(
        item["kind"] == "docker_publish_configuration"
        and item["llm_eligible"] is False
        and item["required"] is True
        for item in ambiguities
    )
    assert "external_requirement" in {
        finding["code"] for finding in result["validation"]["findings"]
    }


def test_docker_build_only_remains_non_pushing_but_needs_exact_approval(tmp_path):
    meta = _meta({
        "task": "Docker@2",
        "inputs": {
            "command": "build",
            "buildContext": ".",
            "Dockerfile": "Dockerfile",
            "tags": "local-test",
        },
    })

    result = PipelineTransformer().transform(meta, tmp_path)
    docker = _docker_step(_workflow(result["workflow_file"]))

    assert str(docker["with"]["push"]).casefold() == "false"
    assert result["production_ready"] is False
    assert any(
        item["kind"] == "unknown_task"
        for item in result["plan"]["ambiguities"]
    )
    assert not any(
        item["kind"] == "docker_publish_configuration"
        for item in result["plan"]["ambiguities"]
    )


def test_validator_rejects_a_build_and_push_workflow_tampered_to_build_only(tmp_path):
    meta = _meta({
        "task": "Docker@2",
        "inputs": {"command": "buildAndPush", "tags": "latest"},
    })
    plan = PipelineConversionPlanner().plan(meta)
    generated = PipelineTransformer()._transform_deterministic(meta, tmp_path)
    workflow = _workflow(generated["workflow_file"])
    _docker_step(workflow)["with"]["push"] = "false"
    tampered = tmp_path / "tampered.yml"
    tampered.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    report = PipelineWorkflowValidator().validate(tampered, meta, plan, {})

    assert report.valid is False
    assert "docker_push_semantics_lost" in {
        finding.code for finding in report.findings
    }


def test_schedule_cron_is_preserved_but_filters_exclusions_and_changes_only_block_readiness(
    tmp_path,
):
    source = """
schedules:
  - cron: '15 3 * * MON'
    branches:
      include: [main, release/*]
      exclude: [release/old]
    always: false
steps:
  - bash: make test
"""
    meta = PipelineMetadataExtractor().extract_yaml_pipeline(
        "Payments",
        {"id": 91, "name": "Fidelity Build"},
        {"configuration": {"repository": {
            "id": "repo-id",
            "name": "api",
            "defaultBranch": "refs/heads/main",
        }}},
        {},
        source,
        [],
        [],
    )

    result = PipelineTransformer().transform(meta, tmp_path)
    workflow = _workflow(result["workflow_file"])
    ambiguity = next(
        item for item in result["plan"]["ambiguities"]
        if item["location"] == ["trigger_schedules", 0]
    )

    assert meta.trigger_schedules[0]["branch_filters"] == ["main", "release/*"]
    assert meta.trigger_schedules[0]["excluded_branches"] == ["release/old"]
    assert _triggers(workflow)["schedule"] == [{"cron": "15 3 * * MON"}]
    assert ambiguity["kind"] == "source_semantics_review"
    assert ambiguity["llm_eligible"] is False
    assert result["validation"]["valid"] is True
    assert result["production_ready"] is False
    assert any("branch exclusions" in warning for warning in result["warnings"])
    assert any("always: false" in warning for warning in result["warnings"])


def test_schedule_with_always_true_and_no_branch_semantics_is_deterministic(tmp_path):
    meta = _meta(schedules=[{
        "cron": "0 6 * * 1-5",
        "branch_filters": [],
        "excluded_branches": [],
        "always": True,
    }])

    result = PipelineTransformer().transform(meta, tmp_path)
    workflow = _workflow(result["workflow_file"])

    assert _triggers(workflow)["schedule"] == [{"cron": "0 6 * * 1-5"}]
    assert result["production_ready"] is True
    assert not any(
        item["location"] == ["trigger_schedules", 0]
        for item in result["plan"]["ambiguities"]
    )


def test_validator_rejects_schedule_semantics_without_planner_review_boundary(
    tmp_path,
):
    meta = _meta(schedules=[{
        "cron": "0 6 * * 1-5",
        "branch_filters": ["release/*"],
        "excluded_branches": ["release/old"],
        "always": False,
    }])
    approved = PipelineConversionPlanner().plan(meta)
    unsafe_plan = ConversionPlan(
        plan_id="plan-without-schedule-boundary",
        source_fingerprint=approved.source_fingerprint,
        ruleset_version=approved.ruleset_version,
        pipeline_id=approved.pipeline_id,
        pipeline_name=approved.pipeline_name,
        mode=ConversionMode.DETERMINISTIC,
        deterministic_steps=approved.deterministic_steps,
        expected_executable_steps=approved.expected_executable_steps,
        ambiguities=(),
    )
    generated = PipelineTransformer()._transform_deterministic(meta, tmp_path)

    report = PipelineWorkflowValidator().validate(
        generated["workflow_file"], meta, unsafe_plan, {}
    )

    assert report.valid is False
    assert "schedule_review_boundary_missing" in {
        finding.code for finding in report.findings
    }


def test_classic_schedule_exclusions_are_retained_in_normalized_metadata():
    build_definition = {
        "repository": {
            "id": "repo-id",
            "name": "api",
            "defaultBranch": "refs/heads/main",
        },
        "schedules": [{
            "daysToBuild": 2,
            "branchFilters": [
                "+refs/heads/main",
                "-refs/heads/release/old",
            ],
            "scheduleOnlyWithChanges": True,
            "startHours": 3,
            "startMinutes": 15,
        }],
        "process": {"phases": []},
    }

    meta = PipelineMetadataExtractor().extract_classic_build_pipeline(
        "Payments",
        {"id": 92, "name": "Classic"},
        build_definition,
        [],
        [],
    )

    schedule = meta.trigger_schedules[0]
    assert schedule["branch_filters"] == ["main"]
    assert schedule["excluded_branches"] == ["release/old"]
    assert schedule["always"] is False
