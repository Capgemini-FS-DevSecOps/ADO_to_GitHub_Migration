"""Tests for multi-job graph preservation."""
from __future__ import annotations

import yaml

from ado2gh.models import PipelineMetadata, PipelineStage, PipelineType
from ado2gh.pipelines.transform.job_graph import build_multi_stage_jobs


def _noop_map(step, warnings, unsupported):
    task = step.get("task", "")
    if task:
        return {"name": task, "run": f"echo {task}"}
    return None


def test_multi_job_stage_preserves_separate_jobs():
    meta = PipelineMetadata(
        pipeline_id=1,
        pipeline_name="multi-job",
        pipeline_type=PipelineType.YAML,
        stages=[
            PipelineStage(
                name="build",
                jobs=[
                    {
                        "job": "compile",
                        "steps": [{"task": "CmdLine@2", "inputs": {"script": "build"}}],
                    },
                    {
                        "job": "test",
                        "dependsOn": ["compile"],
                        "steps": [{"task": "CmdLine@2", "inputs": {"script": "test"}}],
                    },
                ],
            ),
        ],
    )
    warnings: list[str] = []
    unsupported: list[str] = []
    jobs = build_multi_stage_jobs(meta, warnings, unsupported, _noop_map)

    assert len(jobs) == 2
    compile_id = "build_compile"
    test_id = "build_test"
    assert compile_id in jobs
    assert test_id in jobs
    assert jobs[test_id].get("needs") == [compile_id]


def test_golden_yaml_multi_job_transform(tmp_path):
    """Golden-file style: two-job stage produces two GHA jobs with needs edge."""
    from ado2gh.pipelines.transform.transformer import PipelineTransformer

    meta = PipelineMetadata(
        pipeline_id=99,
        pipeline_name="Golden Multi Job",
        pipeline_type=PipelineType.YAML,
        stages=[
            PipelineStage(
                name="ci",
                jobs=[
                    {"job": "build", "steps": [{"task": "NodeTool@0", "inputs": {"versionSpec": "18"}}]},
                    {"job": "deploy", "dependsOn": ["build"],
                     "steps": [{"task": "AzureCLI@2", "inputs": {"inlineScript": "echo deploy"}}]},
                ],
            ),
        ],
    )
    transformer = PipelineTransformer()
    out = tmp_path / "workflows"
    result = transformer.transform(meta, output_dir=out)
    text = result["workflow_file"].read_text(encoding="utf-8")
    yaml_body = text.split("# Generated:", 1)[-1].split("\n", 2)[-1]
    workflow = yaml.safe_load(yaml_body)
    assert len(workflow["jobs"]) == 2
    deploy_job = next(v for k, v in workflow["jobs"].items() if "deploy" in k)
    assert "needs" in deploy_job
