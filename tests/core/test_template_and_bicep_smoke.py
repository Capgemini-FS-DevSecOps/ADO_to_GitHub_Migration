"""Smoke tests for template resolver, task scanner, Bicep/ARM mappings, job graph."""
from __future__ import annotations


from ado2gh.models import PipelineMetadata, PipelineStage, PipelineType
from ado2gh.pipelines.resolve.template_resolver import (
    extract_template_refs,
    resolve_template_path,
    resolve_templates,
)
from ado2gh.pipelines.task_scanner import scan_yaml_tasks, scan_service_connection_refs
from ado2gh.pipelines.transform.job_graph import build_multi_stage_jobs
from ado2gh.pipelines.transform.task_registry import lookup_task
from ado2gh.pipelines.transform.transformer import PipelineTransformer
from ado2gh.state.job_store import SQLiteJobStore
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


def test_step_template_refs():
    yaml_with_step_templates = """
steps:
  - template: templates/azure-pipeline-basic.yml
  - template: templates/azure-pipeline-build.yml
"""
    refs = extract_template_refs(yaml_with_step_templates)
    assert "templates/azure-pipeline-basic.yml" in refs
    assert "templates/azure-pipeline-build.yml" in refs


def test_resolve_step_templates_inlines_steps():
    main_yaml = """
trigger:
  branches:
    include:
      - main
pool:
  vmImage: ubuntu-latest
variables:
  buildConfiguration: 'Release'
steps:
- template: templates/azure-pipeline-basic.yml
- template: templates/azure-pipeline-build.yml
"""
    templates = {
        "templates/azure-pipeline-basic.yml": """
steps:
  - script: echo basic
""",
        "templates/azure-pipeline-build.yml": """
steps:
  - task: DotNetCoreCLI@2
    inputs:
      command: build
""",
    }

    def fetch(name, _ref, source_path):
        path = resolve_template_path(name, source_path or "azure-pipelines.yml")
        return templates.get(path)

    resolved = resolve_templates(
        main_yaml, fetch, source_path="azure-pipelines.yml",
    )
    assert "template:" not in resolved
    assert "echo basic" in resolved
    assert "DotNetCoreCLI@2" in resolved


def test_transformer_inlines_step_templates(tmp_path):
    main_yaml = """
pool:
  vmImage: ubuntu-latest
steps:
- template: templates/build.yml
"""
    template_yaml = """
steps:
  - script: npm ci
  - script: npm test
"""

    def fetch(name, _ref, source_path):
        if name == "templates/build.yml":
            return template_yaml
        return None

    meta = PipelineMetadata(
        project="P",
        pipeline_id=3,
        pipeline_name="template-pipe",
        pipeline_type=PipelineType.YAML,
        repo_name="repo",
        yaml_path="azure-pipelines.yml",
        yaml_content=main_yaml,
    )
    result = PipelineTransformer().transform(
        meta, tmp_path, fetch_template=fetch,
    )
    content = result["workflow_file"].read_text()
    assert "npm ci" in content
    assert "npm test" in content
    assert "Unresolved ADO template" not in content


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
