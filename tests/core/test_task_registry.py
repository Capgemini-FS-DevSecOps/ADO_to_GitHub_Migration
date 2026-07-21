"""Tests for ADO task registry."""
from __future__ import annotations

from ado2gh.pipelines.transform.task_registry import (
    ADO_TASK_MAP,
    lookup_task,
    register_task,
    is_run_based,
)


def test_known_tasks_map_to_actions():
    assert lookup_task("NodeTool@0") == "actions/setup-node@v4"
    assert lookup_task("Bash@3") == "run"
    assert is_run_based("Maven@4")


def test_plugin_registry():
    register_task("CustomTask@1", "my-org/custom-action@v1")
    assert lookup_task("CustomTask@1") == "my-org/custom-action@v1"
    assert lookup_task("UnknownTask@9") is None


def test_ado_task_map_has_core_entries():
    assert "Docker@2" in ADO_TASK_MAP
    assert "PublishBuildArtifacts@1" in ADO_TASK_MAP
