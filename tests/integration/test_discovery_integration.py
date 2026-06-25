"""Integration tests for scan workflow (feature 008, US3).

Tests the end-to-end scan workflow: initiate scan → persist results →
retrieve results → verify no wave assignment.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ado2gh.api.discovery_store import DiscoveryStore
from ado2gh.api.models import DiscoveryResult, ScanStatus
from ado2gh.state.db import StateDB


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    return TestClient(app)


class TestScanWorkflow:
    """Integration tests for the simplified scanning workflow."""

    def test_scan_results_persist_and_retrieve(self):
        """Scan results should persist and be retrievable without wave assignment."""
        db = StateDB(":memory:")
        store = DiscoveryStore(db=db)

        # Save some results
        results = [
            DiscoveryResult(
                organization_id="test-org",
                repository_id="repo-1",
                repository_name="Repository 1",
                pipeline_count=3,
                scan_status=ScanStatus.COMPLETED.value,
            ),
            DiscoveryResult(
                organization_id="test-org",
                repository_id="repo-2",
                repository_name="Repository 2",
                pipeline_count=0,
                scan_status=ScanStatus.COMPLETED.value,
            ),
        ]
        count = store.save_results_batch(results)
        assert count == 2

        # Retrieve results
        retrieved = store.get_results(organization_id="test-org")
        assert len(retrieved) == 2
        assert all(r.organization_id == "test-org" for r in retrieved)

        # Verify no wave assignment fields exist in the model
        for r in retrieved:
            assert not hasattr(r, "wave_id")
            assert not hasattr(r, "assigned_phase")

    def test_scan_summary_aggregation(self):
        """Scan summary should aggregate by organization and status."""
        db = StateDB(":memory:")
        store = DiscoveryStore(db=db)

        store.save_results_batch([
            DiscoveryResult(
                organization_id="org-a",
                repository_id="repo-1",
                repository_name="Repo 1",
                scan_status=ScanStatus.COMPLETED.value,
            ),
            DiscoveryResult(
                organization_id="org-a",
                repository_id="repo-2",
                repository_name="Repo 2",
                scan_status=ScanStatus.COMPLETED.value,
            ),
            DiscoveryResult(
                organization_id="org-b",
                repository_id="repo-3",
                repository_name="Repo 3",
                scan_status=ScanStatus.FAILED.value,
            ),
        ])

        summary = store.get_scan_summary()
        assert summary["total_repositories"] == 3
        assert summary["by_organization"]["org-a"] == 2
        assert summary["by_organization"]["org-b"] == 1
        assert summary["by_status"]["completed"] == 2
        assert summary["by_status"]["failed"] == 1

    def test_scan_status_update(self):
        """Scan status should be updatable for individual repos."""
        db = StateDB(":memory:")
        store = DiscoveryStore(db=db)

        store.save_result(DiscoveryResult(
            organization_id="test-org",
            repository_id="repo-1",
            repository_name="Repo 1",
            scan_status=ScanStatus.PENDING.value,
        ))

        store.update_scan_status("test-org", "repo-1", ScanStatus.COMPLETED.value)

        result = store.get_result("test-org", "repo-1")
        assert result is not None
        assert result.scan_status == ScanStatus.COMPLETED.value

    def test_delete_results_by_org(self):
        """Delete should work per-organization."""
        db = StateDB(":memory:")
        store = DiscoveryStore(db=db)

        store.save_results_batch([
            DiscoveryResult(organization_id="org-a", repository_id="r1", repository_name="R1"),
            DiscoveryResult(organization_id="org-b", repository_id="r2", repository_name="R2"),
        ])

        deleted = store.delete_results(organization_id="org-a")
        assert deleted == 1

        remaining = store.get_results()
        assert len(remaining) == 1
        assert remaining[0].organization_id == "org-b"

    def test_results_via_api_have_no_wave_fields(self, client):
        """API results endpoint must not return wave assignment fields."""
        resp = client.get("/v1/discovery/results")
        assert resp.status_code == 200
        data = resp.json()
        for result in data["results"]:
            assert "wave_id" not in result
            assert "assigned_phase" not in result
