"""Tests for enriched ADO org scan and inventory gap helpers."""

from ado2gh.api.migration_scan import build_inventory_gaps, _summarize_service_connections
from ado2gh.api.migration_work_plan import collect_secret_gap_fields


def test_summarize_service_connections():
    rows = _summarize_service_connections([
        {"name": "azure-prod", "type": "azurerm", "id": "1", "isReady": True},
        {"name": "", "type": "docker"},
    ])
    assert len(rows) == 1
    assert rows[0]["name"] == "azure-prod"


def test_build_inventory_gaps():
    gaps = build_inventory_gaps([
        {
            "project": "Demo",
            "service_connections": [{"name": "azure-prod", "type": "azurerm"}],
            "variable_groups": [{"name": "shared-vars", "variable_count": 3}],
        },
    ])
    assert any(g["type"] == "service_connection" for g in gaps)
    assert any(g["type"] == "variable_group" for g in gaps)


def test_collect_secret_gap_fields_from_discovery():
    discovery = {
        "inventory_gaps": [
            {
                "type": "service_connection",
                "project": "Demo",
                "name": "azure-prod",
                "field": "secret_mapping__Demo__azure-prod",
            },
        ],
    }
    work_items = [
        {
            "scope": "secrets",
            "status": "blocked",
            "blocker": "Service connection 'azure-prod' — create matching GitHub secret",
        },
    ]
    fields = collect_secret_gap_fields(discovery, work_items)
    assert len(fields) == 1
    assert fields[0]["name"] == "secret_mapping__Demo__azure-prod"
