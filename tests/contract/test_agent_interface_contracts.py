"""Contract tests for streamlined agent interface (feature 008, US2).

Verifies that the agent API endpoints support the clean conversational
interface without pipeline or PEV indicators.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    return TestClient(app)


class TestAgentInterface:
    """Contract tests for the streamlined agent interface (FR-003, FR-004)."""

    def test_health_endpoint_works(self, client):
        """Health endpoint must be accessible for agent connectivity checks."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_discovery_results_accessible(self, client):
        """Discovery results must be accessible for agent-driven migration."""
        resp = client.get("/v1/discovery/results")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_migration_wave_creation_accessible(self, client):
        """Wave creation must be accessible for agent-driven bulk migration."""
        resp = client.post("/v1/migration/wave", json={
            "name": "Agent Contract Test",
            "repository_ids": [],
            "organization_id": "test-org",
        })
        assert resp.status_code == 201

    def test_pre_migration_form_endpoint_exists(self, client):
        """Pre-migration form generation must be accessible for agent-driven flows."""
        resp = client.post("/v1/migration/pre-migration-form", json={
            "repository_id": "nonexistent",
            "organization_id": "test-org",
        })
        # 404 is expected since repo doesn't exist in discovery
        assert resp.status_code == 404
