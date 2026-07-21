from __future__ import annotations

from pathlib import Path

import yaml

from ado2gh.models import PipelineMetadata
from ado2gh.pipelines.extractor import PipelineMetadataExtractor
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.transformer import PipelineTransformer


def _yaml_meta(source: str) -> PipelineMetadata:
    return PipelineMetadataExtractor().extract_yaml_pipeline(
        "Payments",
        {"id": 301, "name": "Trigger Fidelity"},
        {
            "configuration": {
                "repository": {
                    "id": "repo-id",
                    "name": "api",
                    "type": "TfsGit",
                    "defaultBranch": "refs/heads/main",
                },
                "path": "/azure-pipelines.yml",
            }
        },
        {},
        source,
        [],
        [],
    )


def _workflow(path: Path) -> dict:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _triggers(workflow: dict) -> dict:
    parsed = workflow.get("on", workflow.get(True))
    assert isinstance(parsed, dict)
    return parsed


def _unsupported_fields(meta: PipelineMetadata) -> set[tuple[object, ...]]:
    return {
        ambiguity.location
        for ambiguity in PipelineConversionPlanner().plan(meta).ambiguities
        if ambiguity.kind == "unsupported_trigger_semantics"
    }


def test_yaml_batch_true_is_retained_and_never_classified_deterministic(tmp_path):
    meta = _yaml_meta(
        """
trigger:
  batch: true
  branches:
    include: [main]
steps:
  - bash: make test
"""
    )

    result = PipelineTransformer().transform(meta, tmp_path)
    workflow = _workflow(result["workflow_file"])
    ambiguity = next(
        item for item in result["plan"]["ambiguities"]
        if item["location"] == ["trigger_batch"]
    )

    assert meta.trigger_batch is True
    assert ambiguity["kind"] == "unsupported_trigger_semantics"
    assert ambiguity["required"] is True
    assert ambiguity["llm_eligible"] is False
    assert result["plan"]["ruleset_version"] == "pipeline-pev-9"
    assert result["production_ready"] is False
    assert "concurrency" not in workflow
    assert _triggers(workflow)["push"]["branches"] == [
        "main",
        "!ado2gh/migrated-workflows",
    ]
    assert any("batch=true" in warning for warning in result["warnings"])


def test_classic_negative_branch_and_path_filters_are_not_dropped():
    meta = PipelineMetadataExtractor().extract_classic_build_pipeline(
        "Payments",
        {"id": 302, "name": "Classic Trigger"},
        {
            "repository": {
                "id": "repo-id",
                "name": "api",
                "type": "TfsGit",
                "defaultBranch": "refs/heads/main",
            },
            "triggers": [
                {
                    "triggerType": 2,
                    "branchFilters": [
                        "+refs/heads/main",
                        "-refs/heads/release/old",
                    ],
                    "pathFilters": ["+/src/**", "-/src/legacy/**"],
                    "batchChanges": True,
                },
                {
                    "triggerType": 64,
                    "branchFilters": [
                        "+refs/heads/main",
                        "-refs/heads/dependabot/**",
                    ],
                    "pathFilters": ["+/app/**", "-/app/generated/**"],
                    "autoCancel": True,
                    "drafts": False,
                },
            ],
            "process": {"phases": []},
        },
        [],
        [],
    )

    assert meta.trigger_branch_excludes == ["release/old"]
    assert meta.trigger_path_includes == ["/src/**"]
    assert meta.trigger_path_excludes == ["/src/legacy/**"]
    assert meta.trigger_batch is True
    assert meta.trigger_pr_branch_excludes == ["dependabot/**"]
    assert meta.trigger_pr_path_includes == ["/app/**"]
    assert meta.trigger_pr_path_excludes == ["/app/generated/**"]
    assert meta.trigger_pr_auto_cancel is True
    assert meta.trigger_pr_drafts is False
    assert any(
        "Classic CI branch excludes" in note and "release/old" in note
        for note in meta.migration_notes
    )
    assert any(
        "Classic PR branch excludes" in note and "dependabot/**" in note
        for note in meta.migration_notes
    )
    assert {
        ("trigger_branch_excludes",),
        ("trigger_path_includes",),
        ("trigger_path_excludes",),
        ("trigger_batch",),
        ("trigger_pr_branch_excludes",),
        ("trigger_pr_path_includes",),
        ("trigger_pr_path_excludes",),
        ("trigger_pr_auto_cancel",),
        ("trigger_pr_drafts",),
    }.issubset(_unsupported_fields(meta))


def test_yaml_pr_and_path_semantics_fail_closed_without_broadening_claims(tmp_path):
    meta = _yaml_meta(
        """
trigger:
  branches:
    include: [main]
    exclude: [release/old]
  paths:
    include: [src/**]
    exclude: [src/generated/**]
pr:
  autoCancel: true
  drafts: false
  branches:
    include: [main]
    exclude: [dependabot/**]
  paths:
    include: [app/**]
    exclude: [app/generated/**]
steps:
  - bash: make test
"""
    )

    result = PipelineTransformer().transform(meta, tmp_path)
    triggers = _triggers(_workflow(result["workflow_file"]))

    assert meta.trigger_branch_excludes == ["release/old"]
    assert meta.trigger_path_includes == ["src/**"]
    assert meta.trigger_path_excludes == ["src/generated/**"]
    assert meta.trigger_pr_branch_excludes == ["dependabot/**"]
    assert meta.trigger_pr_path_includes == ["app/**"]
    assert meta.trigger_pr_path_excludes == ["app/generated/**"]
    assert meta.trigger_pr_auto_cancel is True
    assert meta.trigger_pr_drafts is False
    assert result["production_ready"] is False
    assert "paths" not in triggers["push"]
    assert "paths-ignore" not in triggers["push"]
    assert "paths" not in triggers["pull_request"]
    assert "paths-ignore" not in triggers["pull_request"]
    assert len(_unsupported_fields(meta)) == 8


def test_pr_auto_cancel_false_and_drafts_true_need_no_inexact_policy(tmp_path):
    meta = _yaml_meta(
        """
trigger: none
pr:
  autoCancel: false
  drafts: true
  branches:
    include: [main]
steps:
  - bash: make test
"""
    )

    result = PipelineTransformer().transform(meta, tmp_path)

    assert meta.trigger_pr_auto_cancel is False
    assert meta.trigger_pr_drafts is True
    assert _unsupported_fields(meta) == set()
    assert result["production_ready"] is True


def test_push_trigger_excludes_the_exact_configured_review_branch_last(tmp_path):
    meta = _yaml_meta(
        """
trigger:
  branches:
    include: [main, release/*]
steps:
  - bash: make test
"""
    )
    transformer = PipelineTransformer(
        review_staging_branch="review/workflow-migration"
    )

    result = transformer.transform(meta, tmp_path)
    workflow = _workflow(result["workflow_file"])
    push = _triggers(workflow)["push"]

    assert push == {
        "branches": [
            "main",
            "release/*",
            "!review/workflow-migration",
        ]
    }
    assert transformer.review_staging_branch_exclusion == (
        "!review/workflow-migration"
    )
    expected_guard = (
        "github.ref != 'refs/heads/review/workflow-migration' && "
        "github.head_ref != 'review/workflow-migration'"
    )
    assert transformer.review_staging_job_guard == expected_guard
    assert workflow["jobs"]
    assert all(
        job.get("if") == expected_guard
        for job in workflow["jobs"].values()
    )


def test_trigger_parity_metadata_survives_inventory_round_trip():
    original = _yaml_meta(
        """
trigger:
  batch: true
  branches:
    include: [main]
    exclude: [legacy]
  paths:
    include: [src/**]
    exclude: [src/generated/**]
pr:
  autoCancel: true
  drafts: false
  branches:
    include: [main]
    exclude: [release/**]
  paths:
    include: [app/**]
    exclude: [app/generated/**]
steps:
  - bash: make test
"""
    )

    restored = PipelineMetadata.from_dict(original.to_dict())

    assert restored.trigger_batch is True
    assert restored.trigger_branch_excludes == ["legacy"]
    assert restored.trigger_path_includes == ["src/**"]
    assert restored.trigger_path_excludes == ["src/generated/**"]
    assert restored.trigger_pr_branch_excludes == ["release/**"]
    assert restored.trigger_pr_path_includes == ["app/**"]
    assert restored.trigger_pr_path_excludes == ["app/generated/**"]
    assert restored.trigger_pr_auto_cancel is True
    assert restored.trigger_pr_drafts is False
