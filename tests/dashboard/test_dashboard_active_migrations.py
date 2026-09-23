"""Dashboard active migration snapshot."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from ado2gh.api.pipeline_runner import PipelineRunStore


def test_dashboard_includes_active_migrations():
    from services.accelerator_api.main import app

    PipelineRunStore._runs.clear()
    run = PipelineRunStore.create(
        "Wave 1 migration",
        dry_run=False,
        phase="pilot",
        wave_id=1,
        started_by_username="operator1",
        started_by_display_name="Operator One",
    )
    run.status = "running"
    run.steps[0].status = "running"

    mock_db = MagicMock()
    mock_db.get_migration_repo_counts.return_value = {
        "total_repos": 10,
        "completed_repos": 3,
        "failed_repos": 1,
        "total_pipelines": 5,
    }
    mock_db.inventory_count.return_value = 12
    mock_db.get_all_phase_gates.return_value = []

    with patch("services.accelerator_api.main.create_state_db", return_value=mock_db):
        client = TestClient(app)
        resp = client.get("/v1/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["active_migrations"]) == 1
    item = body["active_migrations"][0]
    assert item["id"] == run.id
    assert item["name"] == "Wave 1 migration"
    assert item["status"] == "running"
    assert item["started_by_display_name"] == "Operator One"
    assert item["current_step"] == run.steps[0].label
