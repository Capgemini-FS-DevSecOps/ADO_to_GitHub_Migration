"""Agent-friendly pipeline migration tools.

This module partitions pipeline migration into small steps:
- transform (pure-ish): PipelineMetadata -> workflow dict + notes + warnings
- render: workflow dict -> YAML text
- write: YAML + notes -> files under .github/workflows (or output dir)

The CLI/orchestrators can call these, and an AI agent can reason about them
as distinct capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ado2gh.models import PipelineMetadata
from ado2gh.pipelines import transformer as transformer_mod


@dataclass(frozen=True)
class PipelineTransformSpec:
    workflow: dict[str, Any]
    warnings: list[str]
    unsupported_tasks: list[str]
    notes_markdown: str
    metrics: dict[str, Any]


@dataclass(frozen=True)
class PipelineWriteArtifacts:
    workflow_file: str
    notes_file: str


def transform_pipeline(meta: PipelineMetadata) -> PipelineTransformSpec:
    """Create a workflow spec from normalized pipeline metadata."""
    transformer = transformer_mod.PipelineTransformer()
    return transformer.transform_to_spec(meta)


def write_pipeline_artifacts(*, spec: PipelineTransformSpec, output_dir: Path, file_stem: str) -> PipelineWriteArtifacts:
    """Write workflow + notes to disk.

    `output_dir` typically points to `<repo>/.github/workflows`.
    """
    transformer = transformer_mod.PipelineTransformer()
    workflow_path, notes_path = transformer.write_spec(
        spec,
        output_dir=output_dir,
        file_stem=file_stem,
    )
    return PipelineWriteArtifacts(str(workflow_path), str(notes_path))


def transform_and_write_pipeline(
    *,
    meta: PipelineMetadata,
    output_dir: Path,
) -> dict[str, Any]:
    """Transform a pipeline and write artifacts.

    If templates were detected/resolved, this writes:
    - root workflow: <pipeline>.yml
    - template workflows: <pipeline>__<template>.yml

    Returns JSON-friendly paths.
    """
    transformer = transformer_mod.PipelineTransformer()
    if getattr(meta, "template_nodes", None):
        return transformer.transform_many(meta, output_dir)
    return transformer.transform(meta, output_dir)
