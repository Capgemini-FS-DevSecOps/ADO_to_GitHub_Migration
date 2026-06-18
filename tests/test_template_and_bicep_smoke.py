"""Smoke tests for template resolver, task scanner, Bicep/ARM mappings, job graph."""
from __future__ import annotations


from ado2gh.models import PipelineMetadata, PipelineStage, PipelineType
from ado2gh.pipelines.resolve.template_resolver import extract_template_refs
from ado2gh.pipelines.task_scanner import scan_yaml_tasks, scan_service_connection_refs
from ado2gh.pipelines.transform.job_graph import build_multi_stage_jobs
from ado2gh.pipelines.transform.task_registry import lookup_task
from ado2gh.pipelines.transform.transformer import PipelineTransformer
from ado2gh.infra.state.job_store import SQLiteJobStore
from ado2gh.api.contracts import JobTypeEnum as JobType


SAMPLE_YAML = """
trigger:
  - main
stages:
  - stage: Build
    jobs:
      - job: compile
        steps:
          - task: NodeTool@0
            inputs:
              versionSpec: '18.x'
          - task: AzureResourceManagerTemplateDeployment@3
            inputs:
              connectedServiceName: my-azure-sc
              templateLocation: 'Linked artifact'
      - job: test
        dependsOn: compile
        steps:
          - task: CmdLine@2
            inputs:
              script: npm test
"""


def test_arm_task_mapped():
    assert lookup_task("AzureResourceManagerTemplateDeployment@3") == "azure/arm-deploy@v2"
    assert lookup_task("AzureResourceGroupDeployment@2") == "azure/arm-deploy@v2"


def test_task_scanner_finds_unsupported():
    all_tasks, unsupported = scan_yaml_tasks(SAMPLE_YAML)
    assert "NodeTool@0" in all_tasks
    assert "AzureResourceManagerTemplateDeployment@3" in all_tasks
    assert "NodeTool@0" not in unsupported
    assert "AzureResourceManagerTemplateDeployment@3" not in unsupported


def test_service_connection_scan():
    refs = scan_service_connection_refs(SAMPLE_YAML)
    assert "my-azure-sc" in refs


def test_template_refs():
    yaml_with_extends = """
extends:
  template: azure-pipelines/ci.yml
stages:
  - stage: Build
    jobs:
      - job: x
        steps:
          - script: echo hi
"""
    refs = extract_template_refs(yaml_with_extends)
    assert "azure-pipelines/ci.yml" in refs


def test_multi_job_graph_preserves_needs():
    meta = PipelineMetadata(
        project="P",
        pipeline_id=1,
        pipeline_name="multi",
        pipeline_type=PipelineType.CLASSIC,
        repo_name="repo",
        stages=[
            PipelineStage(
                name="build",
                jobs=[
                    {"job": "compile", "steps": [{"task": "CmdLine@2", "inputs": {"script": "build"}}]},
                    {"job": "test", "dependsOn": "compile", "steps": [{"task": "CmdLine@2", "inputs": {"script": "test"}}]},
                ],
            ),
        ],
    )
    warnings: list[str] = []
    unsupported: list[str] = []

    def map_step(step, w, u):
        return {"run": step.get("inputs", {}).get("script", "echo")}

    jobs = build_multi_stage_jobs(meta, warnings, unsupported, map_step)
    assert len(jobs) == 2
    test_job = next(j for j in jobs.values() if j["name"] == "test")
    assert "needs" in test_job


def test_transformer_yaml_pipeline(tmp_path):
    meta = PipelineMetadata(
        project="P",
        pipeline_id=2,
        pipeline_name="yaml-pipe",
        pipeline_type=PipelineType.YAML,
        repo_name="repo",
        yaml_content=SAMPLE_YAML,
    )
    result = PipelineTransformer().transform(meta, tmp_path)
    assert result["workflow_file"].exists()
    content = result["workflow_file"].read_text()
    assert "azure/arm-deploy@v2" in content or "NodeTool" in content


def test_sqlite_job_store(tmp_path):
    store = SQLiteJobStore(str(tmp_path / "jobs.db"))
    job = store.enqueue(JobType.DISCOVER, {"config_path": "migration.yaml"}, "key-1")
    assert job.id
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.job_type == JobType.DISCOVER
    dup = store.enqueue(JobType.DISCOVER, {}, "key-1")
    assert dup.id == job.id
