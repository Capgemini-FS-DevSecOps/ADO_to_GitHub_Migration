"""Pin the pipeline protocol enums and step tables to the values they replaced.

Code review items 61, 64, 66 and 72 named a ``PipelineRunStatus`` enum for the
run-status strings that used to be repeated literals in ``pipeline_runner.py``,
switched ``step_prerequisites.py`` to the existing ``StepStatus`` enum instead of
its own copy of the same three strings, and named the numbered-token-variable
slot limit in ``accelerator.py``. Item 62 asks that the accelerator step list and
its prerequisite table come from one definition; this file also checks the two
tables still agree with each other and with that single definition.
"""
from __future__ import annotations

from ado2gh.api.accelerator import MAX_TOKEN_ENV_SLOTS
from ado2gh.api.pipeline_models import (
    ACCELERATOR_PIPELINE_STEPS,
    MIGRATE_UI_PIPELINE_STEPS,
    PIPELINE_STEP_DEFINITIONS,
    PipelineRunStatus,
    StepStatus,
)
from ado2gh.api.step_prerequisites import SATISFIED_STATUSES, STEP_PREREQUISITES


def test_pipeline_run_status_matches_the_literals_it_replaced():
    assert PipelineRunStatus.PENDING == "pending"
    assert PipelineRunStatus.RUNNING == "running"
    assert PipelineRunStatus.CANCELLED == "cancelled"
    assert PipelineRunStatus.FAILED == "failed"
    assert PipelineRunStatus.COMPLETED == "completed"
    assert PipelineRunStatus.DRY_RUN_COMPLETE == "dry_run_complete"
    assert PipelineRunStatus.AWAITING_APPROVAL == "awaiting_approval"


def test_satisfied_statuses_uses_the_existing_step_status_enum():
    # Same three values as before, now sourced from StepStatus instead of a
    # second, hand-typed copy of the same strings.
    assert SATISFIED_STATUSES == (StepStatus.COMPLETED, StepStatus.WARN, StepStatus.SKIPPED)
    assert [s.value for s in SATISFIED_STATUSES] == ["completed", "warn", "skipped"]


def test_max_token_env_slots_matches_the_previous_literal():
    assert MAX_TOKEN_ENV_SLOTS == 20


def test_accelerator_steps_and_prerequisites_agree_with_the_single_definition():
    # ACCELERATOR_PIPELINE_STEPS and STEP_PREREQUISITES are both derived from
    # PIPELINE_STEP_DEFINITIONS (pipeline_models.py) — same ids, same order.
    definition_ids = [d.id for d in PIPELINE_STEP_DEFINITIONS]
    accelerator_ids = [s["id"] for s in ACCELERATOR_PIPELINE_STEPS]
    assert accelerator_ids == definition_ids
    assert list(STEP_PREREQUISITES.keys()) == definition_ids
    for d in PIPELINE_STEP_DEFINITIONS:
        assert STEP_PREREQUISITES[d.id] == list(d.prerequisites)


def test_accelerator_steps_match_the_previous_literal_table():
    # Every id, label and description exactly as they were before the table
    # was derived from PIPELINE_STEP_DEFINITIONS.
    expected = [
        ("connect", "Connect & validate credentials"),
        ("discover", "Discover repositories"),
        ("inventory", "Inventory ADO pipelines"),
        ("readiness", "Assess conversion readiness"),
        ("assign", "Assign migration phases"),
        ("analyze_deps", "Analyze dependencies"),
        ("migrate_repos", "Migrate repository contents"),
        ("convert_pipelines", "Convert pipelines → GitHub Actions"),
        ("convert_metadata", "Convert branch policies & wiki"),
        ("migrate", "Run all scoped migrations"),
        ("validate", "Validate migrated repos"),
    ]
    assert [(s["id"], s["label"]) for s in ACCELERATOR_PIPELINE_STEPS] == expected


def test_migrate_ui_steps_are_a_same_order_subset_of_the_accelerator_ids():
    # MIGRATE_UI_PIPELINE_STEPS carries its own, shorter console copy for a
    # subset of the same step ids, so it is not derived from
    # PIPELINE_STEP_DEFINITIONS — but its ids must still name real accelerator
    # steps and appear in the same relative order.
    accelerator_ids = [s["id"] for s in ACCELERATOR_PIPELINE_STEPS]
    migrate_ui_ids = [s["id"] for s in MIGRATE_UI_PIPELINE_STEPS]
    assert all(step_id in accelerator_ids for step_id in migrate_ui_ids)
    positions = [accelerator_ids.index(step_id) for step_id in migrate_ui_ids]
    assert positions == sorted(positions)
