"""Pipeline readiness report enrichment and API behavior."""
from __future__ import annotations

import json

from ado2gh.models import PipelineComplexity, PipelineMetadata, PipelineType
from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
from ado2gh.state.db import StateDB


def _sample_meta(
    project: str,
    pipeline_id: int,
    name: str,
    *,
    pipe_type: PipelineType = PipelineType.YAML,
    complexity: PipelineComplexity = PipelineComplexity.SIMPLE,
) -> PipelineMetadata:
    return PipelineMetadata(
        project=project,
        pipeline_id=pipeline_id,
        pipeline_name=name,
        pipeline_type=pipe_type,
        repo_name="repo-a",
        complexity=complexity,
    )


def test_readiness_includes_classification_and_migration_status(tmp_path):
    db = StateDB(str(tmp_path / "ready.db"))
    meta = _sample_meta("Proj", 101, "build-ci")
    db.upsert_pipeline_inventory(meta)
    db.upsert_pipeline_migration(
        1,
        meta,
        "gh-org",
        "repo-a",
        __import__("ado2gh.models", fromlist=["MigrationStatus"]).MigrationStatus.COMPLETED,
        workflow_file=".github/workflows/build-ci.yml",
    )

    report = PipelineReadinessReport(db).generate(
        migration_lookup=db.get_latest_pipeline_migrations(),
    )
    assert report["total_pipelines"] == 1
    pipe = report["pipelines"][0]
    assert pipe["classification"] == "auto"
    assert pipe["migration_status"] == "migrated"
    assert pipe["workflow_file"] == ".github/workflows/build-ci.yml"


def test_manual_conversion_when_blockers(tmp_path):
    db = StateDB(str(tmp_path / "manual.db"))
    meta = _sample_meta("Proj", 2, "deploy", complexity=PipelineComplexity.COMPLEX)
    meta.unsupported_tasks = ["AzureKeyVault@2"]
    db.upsert_pipeline_inventory(meta)

    report = PipelineReadinessReport(db).generate()
    assert report["manual"] == 1
    assert report["pipelines"][0]["classification"] == "manual"


def test_readiness_from_inventory_rows(tmp_path):
    db = StateDB(str(tmp_path / "inv.db"))
    meta = _sample_meta("P", 9, "release-flow", pipe_type=PipelineType.RELEASE)
    db.upsert_pipeline_inventory(meta)

    rows = db.get_all_inventory()
    assert len(rows) == 1

    report = PipelineReadinessReport(db).generate()
    assert report["total_pipelines"] == 1
    assert report["pipelines"][0]["pipeline_name"] == "release-flow"
