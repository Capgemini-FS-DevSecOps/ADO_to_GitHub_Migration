"""Pipeline run store and dry-run behavior."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.api.pipeline_runner import PipelineRunStore, enrich_pipeline_run_dict
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import MigrationScope, RepoConfig


def test_list_runs_paginated():
    PipelineRunStore._runs.clear()
    for i in range(25):
        PipelineRunStore.create(f"run-{i}", dry_run=i % 2 == 0, phase="poc", wave_id=None)

    page0, total = PipelineRunStore.list_runs(limit=10, offset=0)
    page1, _ = PipelineRunStore.list_runs(limit=10, offset=10)
    page2, _ = PipelineRunStore.list_runs(limit=10, offset=20)

    assert total == 25
    assert len(page0) == 10
    assert len(page1) == 10
    assert len(page2) == 5
    assert page0[0].created_at >= page0[-1].created_at


def test_list_active_runs_excludes_finished():
    PipelineRunStore._runs.clear()
    active = PipelineRunStore.create(
        "active-run", dry_run=False, phase="poc", wave_id=None,
        started_by_username="alice", started_by_display_name="Alice Admin",
    )
    active.status = "running"
    finished = PipelineRunStore.create("done-run", dry_run=True, phase="poc", wave_id=None)
    finished.status = "completed"

    rows = PipelineRunStore.list_active_runs()
    assert len(rows) == 1
    assert rows[0].id == active.id
    assert rows[0].started_by_display_name == "Alice Admin"


def test_current_step_label():
    PipelineRunStore._runs.clear()
    run = PipelineRunStore.create("step-test", dry_run=True, phase="poc", wave_id=None)
    run.steps[0].status = "completed"
    run.steps[1].status = "running"
    assert run.current_step_label() == run.steps[1].label
    run.steps[1].status = "completed"
    run.steps[2].status = "pending"
    assert run.current_step_label() == run.steps[2].label


def test_dry_run_pipeline_finishes_with_dry_run_complete_status():
    from ado2gh.api.pipeline_runner import MIGRATE_UI_PIPELINE_STEPS, PipelineRunner

    PipelineRunStore._runs.clear()
    run = PipelineRunStore.create(
        "dry-test", dry_run=True, phase="poc", wave_id=None,
        step_defs=MIGRATE_UI_PIPELINE_STEPS,
    )
    settings = MagicMock()
    settings.load.return_value.advanced = MagicMock(
        config_path="migration.yaml", db_path="migration_state.db",
    )
    pr = PipelineRunner(settings=settings)

    with patch.object(pr, "_step_connect"), patch.object(pr, "_step_migrate"), patch.object(
        pr, "_step_validate",
    ):
        pr._execute(run.id, ["connect", "migrate", "validate"])

    finished = PipelineRunStore.get(run.id)
    assert finished is not None
    assert finished.status == "dry_run_complete"


def test_migration_engine_skips_db_writes_on_dry_run():
    db = MagicMock()
    engine = MigrationEngine(
        {"migration_strategy": "mirror"},
        MagicMock(),
        MagicMock(),
        db,
        dry_run=True,
    )
    repo = RepoConfig(
        ado_project="P",
        ado_repo="r1",
        gh_org="gh",
        gh_repo="r1",
        scopes=[MigrationScope.REPO.value],
    )
    with patch("ado2gh.core.migration_engine.SCOPE_REGISTRY") as reg:
        handler = MagicMock()
        handler.migrate.return_value = MagicMock(
            stats={"dry_run": True}, failed=0,
        )
        reg.get.return_value = handler
        engine.migrate_repo(1, repo)

    db.upsert_migration.assert_not_called()


def test_enrich_pipeline_run_dict_auto_approved_for_admin_live():
    run = PipelineRunStore.create(
        "live-admin",
        dry_run=False,
        phase="poc",
        wave_id=None,
        started_by_username="admin",
        started_by_display_name="Admin User",
    )
    run.live_approval_status = "auto_approved"
    enriched = enrich_pipeline_run_dict(run.to_dict())
    assert enriched["started_by_label"] == "Admin User"
    assert enriched["approved_by_label"] == "Auto-approved"


def test_enrich_pipeline_run_dict_operator_pending():
    run = PipelineRunStore.create(
        "live-op",
        dry_run=False,
        phase="poc",
        wave_id=None,
        started_by_display_name="Operator One",
    )
    run.live_approval_status = "pending"
    run.status = "awaiting_approval"
    enriched = enrich_pipeline_run_dict(run.to_dict())
    assert enriched["started_by_label"] == "Operator One"
    assert enriched["approved_by_label"] == "Awaiting approval"


def test_enrich_pipeline_run_dict_dry_run_not_required():
    run = PipelineRunStore.create(
        "dry",
        dry_run=True,
        phase="poc",
        wave_id=None,
        started_by_display_name="Operator One",
    )
    run.live_approval_status = "not_required"
    enriched = enrich_pipeline_run_dict(run.to_dict())
    assert enriched["approved_by_label"] == "Not required (dry run)"
