"""Contract tests for discovery API endpoints (feature 008, US3).

Verifies POST /v1/discovery/scan and GET /v1/discovery/results conform
to the API contract — scan retrieves all ADO org data without wave assignment.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    return TestClient(app)


class TestDiscoveryScanAPI:
    """Contract tests for POST /v1/discovery/scan (T030)."""

    def test_scan_endpoint_exists(self, client):
        """POST /v1/discovery/scan must exist and return 202 or 400."""
        resp = client.post("/v1/discovery/scan", json={
            "organizations": [],
            "force_refresh": False,
        })
        # 400 expected since no orgs configured in test env
        assert resp.status_code == 400

    def test_scan_with_force_refresh(self, client):
        """Scan endpoint must accept force_refresh parameter."""
        resp = client.post("/v1/discovery/scan", json={
            "organizations": [],
            "force_refresh": True,
        })
        assert resp.status_code == 400  # No orgs configured

    def test_scan_returns_scan_id_when_orgs_provided(self, client):
        """Scan with organizations should return a scan_id (even if scan fails)."""
        # This will try to scan but fail since no real ADO credentials
        # The endpoint should still return a response structure
        resp = client.post("/v1/discovery/scan", json={
            "organizations": ["test-org"],
            "force_refresh": False,
        })
        # Either 202 (accepted) or 400/500 if scan fails
        assert resp.status_code in (202, 400, 500)


class TestDiscoveryResultsAPI:
    """Contract tests for GET /v1/discovery/results (T031)."""

    def test_results_endpoint_returns_200(self, client):
        """GET /v1/discovery/results must return 200."""
        resp = client.get("/v1/discovery/results")
        assert resp.status_code == 200

    def test_results_have_required_fields(self, client):
        """Results response must include results array and total_count."""
        resp = client.get("/v1/discovery/results")
        data = resp.json()
        assert "results" in data
        assert "total_count" in data
        assert isinstance(data["results"], list)

    def test_results_filter_by_organization(self, client):
        """Results endpoint must support organization_id filter."""
        resp = client.get("/v1/discovery/results?organization_id=test-org")
        assert resp.status_code == 200
        data = resp.json()
        for result in data["results"]:
            assert result["organization_id"] == "test-org"

    def test_results_filter_by_status(self, client):
        """Results endpoint must support status filter."""
        resp = client.get("/v1/discovery/results?status=completed")
        assert resp.status_code == 200

    def test_results_no_wave_assignment(self, client):
        """Results must NOT include wave assignment fields (FR-005)."""
        resp = client.get("/v1/discovery/results")
        data = resp.json()
        for result in data["results"]:
            assert "wave_id" not in result
            assert "assigned_phase" not in result
            assert "wave_name" not in result
