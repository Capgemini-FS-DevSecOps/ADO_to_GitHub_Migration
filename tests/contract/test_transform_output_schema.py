"""The shape of the workflow YAML the transformer emits (COV-DRIFT-006).

The generated workflow is what ops teams run after migration, so its structure
is a contract: the header comment that identifies the source pipeline, the
top-level key order, the trigger block, the env block, and the job skeleton with
its checkout step. A transformer change may add tasks; it must not silently
alter this skeleton.

Everything is driven off ``PipelineMetadata`` fixtures and written to ``tmp_path``
— no network, no real ``data/``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ado2gh.models import (
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    PipelineVariable,
)
from ado2gh.pipelines.transform.transformer import PipelineTransformer

RESULT_KEYS = {"workflow_file", "notes_file", "warnings", "unsupported_tasks"}


def _meta(**overrides) -> PipelineMetadata:
    base = {
        "pipeline_id": 42,
        "pipeline_name": "Payments CI",
        "pipeline_type": PipelineType.YAML,
        "project": "Contoso Core",
        "repo_name": "payments",
        "repo_branch": "main",
        "trigger_branches": ["main", "release/*"],
        "trigger_pr_branches": ["main"],
        "yaml_content": "steps:\n  - task: Bash@3\n    inputs:\n      script: echo hi\n",
    }
    base.update(overrides)
    return PipelineMetadata(**base)


@pytest.fixture
def transform(tmp_path):
    """Run one transform into ``tmp_path`` and return the result plus the parsed YAML."""
    def _run(meta: PipelineMetadata, **kwargs) -> tuple[dict, dict, str]:
        result = PipelineTransformer().transform(meta, tmp_path / "wf", **kwargs)
        text = Path(result["workflow_file"]).read_text(encoding="utf-8")
        return result, yaml.safe_load(text), text

    return _run


# --------------------------------------------------------------------------
# The result mapping
# --------------------------------------------------------------------------


def test_the_result_carries_exactly_the_documented_keys(transform):
    result, _, _ = transform(_meta())
    assert set(result) == RESULT_KEYS
    assert isinstance(result["warnings"], list)
    assert isinstance(result["unsupported_tasks"], list)


def test_both_artefacts_are_written_to_the_requested_directory(tmp_path, transform):
    result, _, _ = transform(_meta())
    assert Path(result["workflow_file"]).is_file()
    assert Path(result["notes_file"]).is_file()
    assert Path(result["workflow_file"]).parent == tmp_path / "wf"


def test_the_output_directory_is_created_when_it_does_not_exist(tmp_path):
    target = tmp_path / "deep" / "nested" / "wf"
    PipelineTransformer().transform(_meta(), target)
    assert target.is_dir()


def test_the_file_name_is_the_slugified_pipeline_name(transform):
    result, _, _ = transform(_meta(pipeline_name="Payments CI / Nightly!"))
    assert Path(result["workflow_file"]).name == "payments_ci___nightly_.yml"


def test_the_notes_file_sits_beside_the_workflow(transform):
    result, _, _ = transform(_meta())
    assert Path(result["notes_file"]).name == "payments_ci_migration_notes.md"


def test_a_consolidated_layout_writes_every_pipeline_to_one_file(tmp_path):
    transformer = PipelineTransformer()
    first = transformer.transform(
        _meta(pipeline_name="One"), tmp_path / "wf", workflow_layout="consolidated",
    )
    second = transformer.transform(
        _meta(pipeline_name="Two"), tmp_path / "wf", workflow_layout="consolidated",
    )
    assert Path(first["workflow_file"]).name == "consolidated_workflows.yml"
    assert first["workflow_file"] == second["workflow_file"]


# --------------------------------------------------------------------------
# The header comment
# --------------------------------------------------------------------------


def test_the_header_names_the_source_pipeline_project_and_repo(transform):
    _, _, text = transform(_meta())
    header = text.split("\n\n", 1)[0]
    assert "Auto-generated GitHub Actions workflow" in header
    assert "Source: ADO pipeline 'Payments CI'" in header
    assert "id=42" in header
    assert "type=yaml" in header
    assert "Project: Contoso Core" in header
    assert "Repo: payments" in header


def test_the_header_is_a_yaml_comment_so_the_document_still_parses(transform):
    _, workflow, text = transform(_meta())
    assert text.startswith("# ---")
    assert isinstance(workflow, dict), "the header broke the YAML document"


# --------------------------------------------------------------------------
# The workflow document
# --------------------------------------------------------------------------


def test_the_top_level_keys_are_emitted_in_the_documented_order(transform):
    _, workflow, _ = transform(
        _meta(variables=[PipelineVariable(name="BUILD_CONFIG", value="Release")]),
    )
    assert list(workflow) == ["name", "on", "env", "jobs"], (
        "the top-level key order changed"
    )


def test_the_workflow_is_named_after_the_ado_pipeline(transform):
    _, workflow, _ = transform(_meta(pipeline_name="Payments CI"))
    assert workflow["name"] == "Payments CI"


def test_the_trigger_key_is_quoted_so_yaml_cannot_read_it_as_a_boolean(transform):
    """Bare ``on:`` parses as ``True`` under YAML 1.1; the emitter quotes it."""
    _, _, text = transform(_meta())
    assert "\n'on':\n" in text


def test_the_trigger_block_carries_the_push_and_pull_request_branches(transform):
    _, workflow, _ = transform(_meta())
    triggers = workflow["on"]
    assert triggers["push"]["branches"] == ["main", "release/*"]
    assert triggers["pull_request"]["branches"] == ["main"]


def test_manual_dispatch_is_always_offered(transform):
    """Operators need a way to run the migrated workflow without a push."""
    _, workflow, _ = transform(_meta())
    assert "workflow_dispatch" in workflow["on"]


def test_pipeline_variables_become_the_workflow_env_block(transform):
    _, workflow, _ = transform(
        _meta(variables=[
            PipelineVariable(name="BUILD_CONFIG", value="Release"),
            PipelineVariable(name="RUNTIME", value="net8.0"),
        ]),
    )
    assert workflow["env"] == {"BUILD_CONFIG": "Release", "RUNTIME": "net8.0"}


def test_a_pipeline_with_no_variables_emits_no_env_block(transform):
    _, workflow, _ = transform(_meta())
    assert "env" not in workflow


def test_every_job_declares_a_runner_and_a_step_list(transform):
    _, workflow, _ = transform(_meta())
    assert workflow["jobs"], "the workflow was emitted with no jobs at all"
    for name, job in workflow["jobs"].items():
        assert job["runs-on"], f"job {name} declares no runner"
        assert isinstance(job["steps"], list) and job["steps"], f"job {name} has no steps"


def test_the_first_step_of_every_job_checks_the_repository_out(transform):
    _, workflow, _ = transform(_meta())
    for name, job in workflow["jobs"].items():
        assert job["steps"][0]["uses"].startswith("actions/checkout@"), (
            f"job {name} does not check the repository out first"
        )


def test_the_converted_steps_follow_the_checkout(transform):
    _, workflow, _ = transform(_meta())
    steps = next(iter(workflow["jobs"].values()))["steps"]
    assert any(step.get("run") == "echo hi" for step in steps)


# --------------------------------------------------------------------------
# The three pipeline types all produce a parseable workflow
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pipeline_type", [PipelineType.YAML, PipelineType.CLASSIC, PipelineType.RELEASE],
)
def test_every_pipeline_type_emits_a_workflow_with_name_triggers_and_jobs(
    transform, pipeline_type,
):
    _, workflow, _ = transform(_meta(pipeline_type=pipeline_type))
    assert workflow["name"] == "Payments CI"
    assert "on" in workflow, "no trigger block was emitted"
    assert workflow["jobs"]


def test_a_classic_pipeline_warns_that_the_conversion_is_best_effort(transform):
    result, _, _ = transform(_meta(pipeline_type=PipelineType.CLASSIC))
    assert any("best-effort conversion" in w for w in result["warnings"])


def test_a_yaml_pipeline_with_no_content_still_emits_a_job(transform):
    _, workflow, _ = transform(_meta(yaml_content=""))
    assert workflow["jobs"]


def test_stages_take_precedence_over_the_raw_yaml(transform):
    meta = _meta(
        stages=[
            PipelineStage(
                name="Build",
                jobs=[{
                    "name": "compile",
                    "steps": [{"task": "Bash@3", "inputs": {"script": "make"}}],
                }],
            ),
        ],
    )
    _, workflow, _ = transform(meta)
    commands = [
        step.get("run")
        for job in workflow["jobs"].values()
        for step in job["steps"]
    ]
    assert "make" in commands
    assert "echo hi" not in commands, "the raw YAML was used although stages were extracted"


# --------------------------------------------------------------------------
# Unsupported tasks are reported, not swallowed
# --------------------------------------------------------------------------


def test_an_unsupported_task_is_named_in_the_result_and_the_workflow(transform):
    result, workflow, _ = transform(
        _meta(yaml_content="steps:\n  - task: SomeVendorTask@7\n"),
    )
    assert "SomeVendorTask@7" in result["unsupported_tasks"]
    assert any("no known GHA equivalent" in w for w in result["warnings"])
    todos = [
        step for job in workflow["jobs"].values() for step in job["steps"]
        if str(step.get("name", "")).startswith("TODO:")
    ]
    assert todos, "an unsupported task vanished from the generated workflow"


def test_unsupported_tasks_recorded_on_the_metadata_are_carried_through(transform):
    result, _, _ = transform(_meta(unsupported_tasks=["EarlierFinding@1"]))
    assert "EarlierFinding@1" in result["unsupported_tasks"]


def test_the_migration_notes_are_written_as_markdown(transform):
    result, _, _ = transform(
        _meta(yaml_content="steps:\n  - task: SomeVendorTask@7\n"),
    )
    notes = Path(result["notes_file"]).read_text(encoding="utf-8")
    assert notes.lstrip().startswith("#")
    assert "Payments CI" in notes


# --------------------------------------------------------------------------
# The emitted document round-trips
# --------------------------------------------------------------------------


def test_the_emitted_yaml_reparses_to_the_same_document(transform):
    _, workflow, text = transform(_meta())
    body = text.split("# ---------------------------------------------------------\n\n", 1)[-1]
    assert yaml.safe_load(body) == workflow


def test_the_workflow_is_written_as_utf8_block_style(transform):
    _, _, text = transform(_meta(pipeline_name="Zahlungen – CI"))
    assert "Zahlungen – CI" in text
    assert "{" not in text.split("\n\n", 1)[1].split("jobs:")[0].replace("{}", ""), (
        "the document was emitted in flow style"
    )
