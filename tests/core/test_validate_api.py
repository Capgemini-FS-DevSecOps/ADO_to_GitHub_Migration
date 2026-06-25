"""Validate API returns normalized repo rows for the UI."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.api.agentic_routes import router as agentic_router
from ado2gh.api.contracts import ValidateResult
from ado2gh.api.settings_store import MigrationProfile, SettingsStore, UISettings


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings_path = tmp_path / "ui_settings.json"
    store = SettingsStore(path=settings_path)
    profile = MigrationProfile(
        id="p1",
        name="Test",
        ado_org_url="https://dev.azure.com/org",
        ado_pat="pat",
        gh_org="gh-org",
        status="active",
    )
    store.save(UISettings(active_profile_id="p1", migration_profiles=[profile]))

    monkeypatch.setenv("ADO2GH_UI_SETTINGS_PATH", str(settings_path))

    from services.accelerator_api import main as accel_main

    app = FastAPI()
    app.include_router(agentic_router)
    # Reuse accelerator validate route on a minimal app
    app.post("/v1/validate")(accel_main.validate)
    return TestClient(app)


def test_validate_api_normalizes_repo_fields(client):
    raw = [{
        "ado_project": "Proj",
        "ado_repo": "repo-a",
        "gh_target": "gh-org/repo-a",
        "overall": "PASS",
        "checks": {
            "head_commit": {"verdict": "PASS", "detail": "SHA match"},
        },
    }]
    validate_result = ValidateResult(total=1, passed=1, failed=0, details=raw)

    with patch("ado2gh.api.validation_run.run_validation", return_value=validate_result):
        resp = client.post("/v1/validate", json={"profile_id": "p1", "phase": "poc"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] == 1
    row = body["results"][0]
    assert row["project"] == "Proj"
    assert row["repo"] == "repo-a"
    assert row["overall"] == "PASS"
    assert row["primary_reason"]
    assert row["gh_target"] == "gh-org/repo-a"
