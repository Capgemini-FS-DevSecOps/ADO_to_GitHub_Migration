"""Regression check for register entry GAP-032 — a secret pipeline variable must never be inlined into generated YAML.

``ado2gh/pipelines/transform/transformer.py::_build_env_block`` (workflow-level
``env``, fed from ``PipelineMetadata.variables``) and
``ado2gh/pipelines/transform/job_graph.py::_build_single_job`` (job-level ``env``,
fed from ``PipelineStage.variables``) are the only two sites in the package that
write a ``PipelineVariable.value`` into a generated workflow. Both are gated on
``is_secret`` and substitute a ``${{ secrets.NAME }}`` reference instead.

Before this module nothing asserted either branch: an inverted condition, or a
merge dropping one of them, would have written a secret value into the workflow
file with the suite green. These tests hold both branches in place, at the choke
point and through the real render path.

Every value below is a fabricated placeholder. No real credential appears here,
and the assertions are written so that a leak fails on the *name* of the
variable rather than by echoing the value (CA-003).
"""
from __future__ import annotations

import yaml

from ado2gh.models import (
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    PipelineVariable,
)
from ado2gh.pipelines.transform.job_graph import build_multi_stage_jobs
from ado2gh.pipelines.transform.transformer import PipelineTransformer

# Fabricated values. The "must-not-be-inlined" marker makes an accidental leak
# obvious in a diff and lets the assertions scan the rendered text for it.
_LEAK_MARKER = "must-not-be-inlined"

WORKFLOW_SECRETS = {
    "API_KEY": f"fake-api-key-{_LEAK_MARKER}-a1b2c3",
    "DB_PASSWORD": f"fake-db-password-{_LEAK_MARKER}-d4e5f6",
}
WORKFLOW_PLAIN = {
    "BUILD_CONFIGURATION": "Release",
    "TARGET_ENV": "staging",
}
STAGE_SECRETS = {
    "DEPLOY_TOKEN": f"fake-deploy-token-{_LEAK_MARKER}-g7h8i9",
}
STAGE_PLAIN = {
    "DEPLOY_REGION": "westeurope",
}


def _variables(plain: dict[str, str], secrets: dict[str, str]) -> list[PipelineVariable]:
    """Build a mixed variable list, secrets interleaved with plain values.

    Args:
        plain: Names and values of the non-secret variables.
        secrets: Names and values of the secret variables.

    Returns:
        The variables, ordered plain-first so that a writer which stops at the
        first secret is still caught by the ones behind it.
    """
    return [PipelineVariable(name=n, value=v) for n, v in plain.items()] + [
        PipelineVariable(name=n, value=v, is_secret=True) for n, v in secrets.items()
    ]


def _meta() -> PipelineMetadata:
    """Build pipeline metadata carrying secrets at both the workflow and stage level.

    Returns:
        Metadata with one stage that declares no ADO jobs, which is the shape
        that routes through the job-level ``env`` writer in ``job_graph``.
    """
    return PipelineMetadata(
        pipeline_id=32,
        pipeline_name="gap-032-secret-variables",
        pipeline_type=PipelineType.YAML,
        variables=_variables(WORKFLOW_PLAIN, WORKFLOW_SECRETS),
        stages=[
            PipelineStage(
                name="deploy",
                variables=_variables(STAGE_PLAIN, STAGE_SECRETS),
            ),
        ],
    )


def _noop_map(step, warnings, unsupported):
    """Map one ADO step, without touching variables.

    Returns:
        ``None`` always — this suite asserts on ``env`` blocks, not on steps.
    """
    return None


def _leaked_names(text: str, secrets: dict[str, str]) -> list[str]:
    """Report which secrets had their value written into the rendered output.

    Args:
        text: Rendered workflow text.
        secrets: Names and values of the secret variables.

    Returns:
        The names whose value appears verbatim. Names, never values, so a
        failure message cannot echo a credential (CA-003).
    """
    return sorted(name for name, value in secrets.items() if value in text)


def test_transform_writes_secret_references_not_values(tmp_path):
    """The workflow file on disk carries `${{ secrets.NAME }}`, never the value."""
    result = PipelineTransformer().transform(_meta(), output_dir=tmp_path)
    text = result["workflow_file"].read_text(encoding="utf-8")

    all_secrets = {**WORKFLOW_SECRETS, **STAGE_SECRETS}
    assert _leaked_names(text, all_secrets) == [], (
        "secret variable values were written into the generated workflow: "
        f"{_leaked_names(text, all_secrets)}"
    )
    assert _LEAK_MARKER not in text
    for name in all_secrets:
        assert f"${{{{ secrets.{name} }}}}" in text, f"{name} lost its secrets reference"

    workflow = yaml.safe_load(text)
    assert workflow["env"] == {
        **WORKFLOW_PLAIN,
        **{name: f"${{{{ secrets.{name} }}}}" for name in WORKFLOW_SECRETS},
    }
    job = next(iter(workflow["jobs"].values()))
    assert job["env"] == {
        **STAGE_PLAIN,
        **{name: f"${{{{ secrets.{name} }}}}" for name in STAGE_SECRETS},
    }


def test_build_env_block_substitutes_a_secrets_reference_for_every_secret():
    """The workflow-level writer never copies a secret value into `env`."""
    env = PipelineTransformer._build_env_block(_meta())

    assert _leaked_names(yaml.safe_dump(env), WORKFLOW_SECRETS) == []
    for name in WORKFLOW_SECRETS:
        assert env[name] == f"${{{{ secrets.{name} }}}}"
    for name, value in WORKFLOW_PLAIN.items():
        assert env[name] == value


def test_stage_variables_become_job_env_with_secrets_referenced():
    """The job-level writer never copies a secret value into a job `env`."""
    jobs = build_multi_stage_jobs(_meta(), [], [], _noop_map)
    job_env = next(iter(jobs.values()))["env"]

    assert _leaked_names(yaml.safe_dump(job_env), STAGE_SECRETS) == []
    for name in STAGE_SECRETS:
        assert job_env[name] == f"${{{{ secrets.{name} }}}}"
    for name, value in STAGE_PLAIN.items():
        assert job_env[name] == value
