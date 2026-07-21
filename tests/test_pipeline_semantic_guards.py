from __future__ import annotations

from pathlib import Path

import yaml

from ado2gh.models import (
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    PipelineVariable,
)
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.transformer import PipelineTransformer


def _meta(step, *, pool="ubuntu-latest", variables=(), parameters=()):
    return PipelineMetadata(
        pipeline_id=91,
        pipeline_name="Semantic guard",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="api",
        variables=list(variables),
        parameters=list(parameters),
        stages=[PipelineStage(
            name="build",
            agent_pool=pool,
            jobs=[{"job": "build", "steps": [step]}],
        )],
    )


def _ambiguity_kinds(meta):
    return {item.kind for item in PipelineConversionPlanner().plan(meta).ambiguities}


def test_build_reason_and_variable_conditions_are_metadata_aware():
    planner = PipelineConversionPlanner()
    declared = {"flavor"}

    assert planner.condition_is_supported(
        "eq(variables['Build.Reason'], 'PullRequest')", declared, set()
    )
    assert not planner.condition_is_supported(
        "eq(variables['Build.Reason'], 'BuildCompletion')", declared, set()
    )
    assert not planner.condition_is_supported(
        "eq(variables['Agent.OS'], 'Linux')", declared, set()
    )
    assert planner.condition_is_supported(
        "eq(variables.flavor, 'release')", declared, set()
    )
    assert PipelineTransformer._map_condition(
        "eq(variables.Build.SourceBranch, 'refs/heads/main')"
    ) == "(github.ref == 'refs/heads/main')"
    assert PipelineTransformer._map_condition(
        "eq(variables['Build.Reason'], 'PullRequest')"
    ) == "(github.event_name == 'pull_request')"
    assert PipelineTransformer._map_condition(
        "eq(variables['expected'], 'Build.SourceBranch')"
    ) == "(env.EXPECTED == 'Build.SourceBranch')"
    assert not planner.condition_is_supported(
        "eq(parameters.Mode, 'release')", set(), {"mode"}
    )


def test_structured_and_automatic_parameters_fail_closed():
    structured = _meta(
        {"bash": "echo test"},
        parameters=[{
            "name": "config", "type": "object", "default": {"deploy": True},
        }],
    )
    assert "unsupported_parameter_semantics" in _ambiguity_kinds(structured)

    automatic = _meta(
        {"bash": "echo ${{ parameters.mode }}"},
        parameters=[{"name": "mode", "type": "string", "default": "safe"}],
    )
    automatic.trigger_branches = ["main"]
    assert "parameter_trigger_semantics" in _ambiguity_kinds(automatic)


def test_ado_runtime_variable_expression_is_not_treated_as_literal():
    meta = _meta(
        {"bash": "echo test"},
        variables=[PipelineVariable(
            "isMain",
            "$[eq(variables['Build.SourceBranch'], 'refs/heads/main')]",
        )],
    )
    assert "variable_expression_semantics" in _ambiguity_kinds(meta)


def test_dotted_macro_uses_exact_indexed_env_lookup(tmp_path):
    meta = _meta(
        {"bash": "echo $(Build.Configuration)"},
        variables=[PipelineVariable("Build.Configuration", "Release")],
    )
    result = PipelineTransformer()._transform_deterministic(meta, Path(tmp_path))
    parsed = yaml.safe_load(result["workflow_file"].read_text(encoding="utf-8"))
    step = parsed["jobs"]["build"]["steps"][1]
    assert step["run"] == "echo ${{ env.BUILD_CONFIGURATION }}"
    assert parsed["env"] == {"BUILD_CONFIGURATION": "Release"}


def test_ado_script_environment_projection_and_collisions_are_exact(tmp_path):
    meta = _meta(
        {"bash": 'test "$MY_VAR" = x'},
        variables=[PipelineVariable("my.var", "x")],
    )
    result = PipelineTransformer()._transform_deterministic(meta, Path(tmp_path))
    parsed = yaml.safe_load(result["workflow_file"].read_text(encoding="utf-8"))
    assert parsed["env"] == {"MY_VAR": "x"}
    assert parsed["jobs"]["build"]["steps"][1]["shell"] == (
        "bash --noprofile --norc {0}"
    )
    assert PipelineTransformer._map_condition(
        "eq(variables.myVar, 'x')"
    ) == "(env.MYVAR == 'x')"

    collision = _meta(
        {"bash": "echo test"},
        variables=[
            PipelineVariable("my.var", "one"),
            PipelineVariable("MY_VAR", "two"),
        ],
    )
    assert "variable_environment_collision" in _ambiguity_kinds(collision)


def test_secret_is_never_in_workflow_global_env(tmp_path):
    meta = _meta(
        {"bash": "echo safe"},
        variables=[PipelineVariable("DEPLOY_TOKEN", "secret", True)],
    )
    result = PipelineTransformer()._transform_deterministic(meta, Path(tmp_path))
    parsed = yaml.safe_load(result["workflow_file"].read_text(encoding="utf-8"))
    assert "DEPLOY_TOKEN" not in parsed.get("env", {})


def test_platform_and_unpreserved_step_semantics_require_exact_approval():
    windows_script = _meta(
        {"script": "echo %PATH%"}, pool="windows-latest"
    )
    assert "unknown_step" in _ambiguity_kinds(windows_script)
    assert "unknown_step" in _ambiguity_kinds(
        _meta({"bash": "echo $PATH"}, pool="windows-latest")
    )
    assert "unknown_task" in _ambiguity_kinds(_meta(
        {"task": "Bash@3", "inputs": {"script": "echo $PATH"}},
        pool="windows-latest",
    ))

    retrying_bash = _meta({
        "bash": "./test.sh",
        "failOnStderr": True,
        "retryCountOnTaskFailure": 3,
    })
    assert "unknown_step" in _ambiguity_kinds(retrying_bash)

    file_task = _meta({
        "task": "Bash@3",
        "inputs": {"filePath": "scripts/build script.sh", "arguments": "--release"},
    })
    assert "unknown_task" in _ambiguity_kinds(file_task)

    for step in (
        {"powershell": "Write-Host ok"},
        {"task": "PowerShell@2", "inputs": {"script": "Write-Host ok"}},
    ):
        assert "unknown_step" in _ambiguity_kinds(_meta(step)) \
            or "unknown_task" in _ambiguity_kinds(_meta(step))


def test_yaml_deployment_environment_is_blocked_without_source_check_inventory():
    meta = PipelineMetadata(
        pipeline_id=92,
        pipeline_name="Deploy",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="api",
        stages=[PipelineStage(
            name="deploy",
            is_deployment=True,
            jobs=[{
                "deployment": "production",
                "environment": "prod",
                "strategy": {"runOnce": {"deploy": {"steps": [
                    {"bash": "echo deploy"},
                ]}}},
            }],
        )],
    )

    assert (
        "deployment_environment_source_checks_unavailable"
        in _ambiguity_kinds(meta)
    )


def test_raw_yaml_grammar_catches_constructs_erased_by_normalization():
    sources = [
        """
variables:
  - template: vars.yml
stages:
  - stage: build
    jobs:
      - job: build
        steps:
          - bash: echo test
""",
        """
stages:
  - template: deploy.yml
  - stage: build
    jobs:
      - job: build
        steps:
          - bash: echo test
""",
        """
stages:
  - stage: release
    trigger: manual
    jobs:
      - job: build
        steps:
          - bash: echo test
""",
        """
pool:
  vmImage: ubuntu-latest
  demands:
    - Agent.Version -gtVersion 3.0
steps:
  - bash: echo test
""",
    ]
    for source in sources:
        meta = _meta({"bash": "echo test"})
        meta.yaml_content = source
        assert "unsupported_source_construct" in _ambiguity_kinds(meta)
