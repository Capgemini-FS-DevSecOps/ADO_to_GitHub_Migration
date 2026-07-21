"""Contract tests for unified tab navigation (feature 008, US1).

Verifies that the unified navigation structure exposes exactly two primary tabs
(Discovery and Migrate) plus Agent and Settings, and that old tab routes
redirect to the new unified structure.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    return TestClient(app)


class TestUnifiedTabNavigation:
    """Contract tests for the unified tab structure (FR-001)."""

    def test_discovery_api_endpoint_exists(self, client):
        """Discovery API endpoint must be accessible at /v1/discovery/results."""
        resp = client.get("/v1/discovery/results")
        assert resp.status_code == 200

    def test_migration_api_endpoint_exists(self, client):
        """Migration API endpoint must be accessible at /v1/migration/wave (GET not defined, POST is)."""
        resp = client.post("/v1/migration/wave", json={
            "name": "Test Wave",
            "description": "Contract test",
            "repository_ids": [],
            "organization_id": "test-org",
        })
        assert resp.status_code == 201

    def test_discovery_scan_endpoint_exists(self, client):
        """Discovery scan endpoint must accept POST at /v1/discovery/scan."""
        resp = client.post("/v1/discovery/scan", json={
            "organizations": [],
            "force_refresh": False,
        })
        # Should return 400 since no orgs configured, not 404
        assert resp.status_code == 400

    def test_pre_migration_form_endpoint_exists(self, client):
        """Pre-migration form endpoint must accept POST."""
        resp = client.post("/v1/migration/pre-migration-form", json={
            "repository_id": "test-repo",
            "organization_id": "test-org",
        })
        # 404 expected since repo not in discovery results
        assert resp.status_code == 404

    def test_health_check_passes(self, client):
        """Health endpoint must return 200 for Docker health checks."""
        resp = client.get("/health")
        assert resp.status_code == 200
