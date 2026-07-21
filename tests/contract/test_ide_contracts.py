"""Contract tests for local IDE agent HTTP and MCP surfaces."""
from fastapi.testclient import TestClient

from services.agent.main import app
from ado2gh.agents.migration_agent.constants import TOOL_CATALOG_VERSION


client = TestClient(app)


def test_health_contract_fields():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "accelerator_reachable" in data
    assert "tool_catalog_version" in data
    assert data["tool_catalog_version"] == TOOL_CATALOG_VERSION
    assert "capabilities" in data or data.get("status") == "degraded"


def test_create_session_contract():
    r = client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "Plan POC dry-run", "dry_run": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"].startswith("ses_")
    assert data["dry_run"] is True
    assert data["status"] in ("idle", "planning", "executing", "completed", "validating")


def test_llm_status_contract():
    r = client.get("/v1/llm/status")
    assert r.status_code == 200
    data = r.json()
    assert "provider" in data
    assert "available" in data
