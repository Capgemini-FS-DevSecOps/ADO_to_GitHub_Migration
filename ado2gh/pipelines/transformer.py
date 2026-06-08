"""Backward-compatible re-export — use ado2gh.pipelines.transform."""
from ado2gh.pipelines.transform.transformer import PipelineTransformer
from ado2gh.pipelines.transform.task_registry import ADO_TASK_MAP

__all__ = ["PipelineTransformer", "ADO_TASK_MAP"]
