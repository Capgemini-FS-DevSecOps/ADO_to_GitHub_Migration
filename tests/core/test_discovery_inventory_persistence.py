"""Discovery inventory persistence across DB reload."""
from pathlib import Path

import pytest

from ado2gh.api.migration_scan import load_scan_results, persist_scan_results
from ado2gh.state.db import StateDB


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> StateDB:
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "state.db"))
    return StateDB(tmp_path / "state.db")


def _scan_with_inventory() -> dict:
    return {
        "scanned_at": "2026-06-18T13:25:15+00:00",
        "gh_org": "ado-to-gh-migration",
        "projects_scanned": 3,
        "repos_scanned": 34,
        "status": "ok",
        "recommendations": {
            "poc": {
                "phase": "poc",
                "repo_count": 1,
                "risk_min": 10,
                "risk_max": 10,
                "rationale": "Low risk",
                "repos": [
                    {
                        "project": "sre-assets-development",
                        "repo_name": "example",
                        "total_score": 10,
                        "assigned_phase": "poc",
                        "pipeline_count": 1,
                    },
                ],
            },
        },
        "project_details": [
            {
                "project": "sre-assets-development",
                "service_connection_count": 5,
                "variable_group_count": 1,
                "environment_count": 1,
                "pipeline_count": 15,
                "service_connections": [{"name": "azure-prod", "type": "azurerm"}],
            },
        ],
        "org_inventory": {
            "total_service_connections": 5,
            "total_variable_groups": 1,
            "total_environments": 1,
            "pipeline_inventory_count": 17,
        },
        "inventory_gaps": [
            {
                "type": "service_connection",
                "project": "sre-assets-development",
                "name": "azure-prod",
                "field": "secret_mapping__sre-assets-development__azure-prod",
            },
        ],
        "pipeline_inventory": {"inventory_count": 17},
    }


def test_discovery_inventory_persists_and_reloads(db: StateDB, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    profile_id = "prof-discovery"
    raw = _scan_with_inventory()
    persist_scan_results(profile_id, raw)

    loaded = load_scan_results(profile_id)
    assert loaded is not None
    assert loaded["org_inventory"]["total_service_connections"] == 5
    assert loaded["project_details"][0]["service_connection_count"] == 5
    assert len(loaded["inventory_gaps"]) == 1

    payload = db.build_profile_scan_payload(profile_id)
    assert payload["org_inventory"]["total_service_connections"] == 5
    assert payload["project_details"][0]["project"] == "sre-assets-development"
