"""Unit tests for DiscoveryStore (feature 008, T076)."""
from __future__ import annotations

import pytest
from ado2gh.api.discovery_store import DiscoveryStore
from ado2gh.api.models import DiscoveryResult, DependencyEdge, ScanStatus
from ado2gh.state.db import StateDB


@pytest.fixture
def store():
    return DiscoveryStore(db=StateDB(":memory:"))


class TestDiscoveryStoreCRUD:
    def test_save_and_get_single_result(self, store):
        result = DiscoveryResult(
            organization_id="test-org",
            repository_id="repo-1",
            repository_name="Repo 1",
            pipeline_count=5,
            scan_status=ScanStatus.COMPLETED.value,
        )
        store.save_result(result)

        retrieved = store.get_result("test-org", "repo-1")
        assert retrieved is not None
        assert retrieved.repository_name == "Repo 1"
        assert retrieved.pipeline_count == 5

    def test_save_batch_results(self, store):
        results = [
            DiscoveryResult(organization_id="org", repository_id=f"repo-{i}", repository_name=f"Repo {i}")
            for i in range(10)
        ]
        count = store.save_results_batch(results)
        assert count == 10

        retrieved = store.get_results(organization_id="org")
        assert len(retrieved) == 10

    def test_get_results_filter_by_status(self, store):
        store.save_result(DiscoveryResult(
            organization_id="org", repository_id="r1", repository_name="R1",
            scan_status=ScanStatus.COMPLETED.value,
        ))
        store.save_result(DiscoveryResult(
            organization_id="org", repository_id="r2", repository_name="R2",
            scan_status=ScanStatus.FAILED.value,
        ))

        completed = store.get_results(status="completed")
        assert len(completed) == 1
        assert completed[0].repository_id == "r1"

    def test_update_scan_status(self, store):
        store.save_result(DiscoveryResult(
            organization_id="org", repository_id="r1", repository_name="R1",
            scan_status=ScanStatus.PENDING.value,
        ))

        store.update_scan_status("org", "r1", ScanStatus.COMPLETED.value)

        result = store.get_result("org", "r1")
        assert result.scan_status == "completed"

    def test_delete_all_results(self, store):
        store.save_results_batch([
            DiscoveryResult(organization_id="a", repository_id="r1", repository_name="R1"),
            DiscoveryResult(organization_id="b", repository_id="r2", repository_name="R2"),
        ])

        deleted = store.delete_results()
        assert deleted == 2
        assert len(store.get_results()) == 0

    def test_delete_by_organization(self, store):
        store.save_results_batch([
            DiscoveryResult(organization_id="a", repository_id="r1", repository_name="R1"),
            DiscoveryResult(organization_id="b", repository_id="r2", repository_name="R2"),
        ])

        deleted = store.delete_results(organization_id="a")
        assert deleted == 1
        remaining = store.get_results()
        assert len(remaining) == 1
        assert remaining[0].organization_id == "b"


class TestDiscoveryStoreSummary:
    def test_summary_empty(self, store):
        summary = store.get_scan_summary()
        assert summary["total_repositories"] == 0
        assert summary["by_status"] == {}
        assert summary["by_organization"] == {}

    def test_summary_with_data(self, store):
        store.save_results_batch([
            DiscoveryResult(organization_id="org-a", repository_id="r1", repository_name="R1",
                            scan_status=ScanStatus.COMPLETED.value),
            DiscoveryResult(organization_id="org-a", repository_id="r2", repository_name="R2",
                            scan_status=ScanStatus.COMPLETED.value),
            DiscoveryResult(organization_id="org-b", repository_id="r3", repository_name="R3",
                            scan_status=ScanStatus.FAILED.value),
        ])

        summary = store.get_scan_summary()
        assert summary["total_repositories"] == 3
        assert summary["by_organization"]["org-a"] == 2
        assert summary["by_organization"]["org-b"] == 1
        assert summary["by_status"]["completed"] == 2
        assert summary["by_status"]["failed"] == 1


class TestDiscoveryStoreDependencies:
    def test_save_and_get_dependency_edges(self, store):
        store.save_dependency_edge(DependencyEdge(
            source_repository_id="repo-a",
            target_repository_id="repo-b",
        ))
        store.save_dependency_edge(DependencyEdge(
            source_repository_id="repo-a",
            target_repository_id="repo-c",
        ))

        edges = store.get_dependency_edges(repository_id="repo-a")
        assert len(edges) == 2
        targets = {e.target_repository_id for e in edges}
        assert targets == {"repo-b", "repo-c"}

    def test_get_all_dependency_edges(self, store):
        store.save_dependency_edge(DependencyEdge(
            source_repository_id="repo-a",
            target_repository_id="repo-b",
        ))
        store.save_dependency_edge(DependencyEdge(
            source_repository_id="repo-c",
            target_repository_id="repo-d",
        ))

        all_edges = store.get_dependency_edges()
        assert len(all_edges) == 2


class TestDiscoveryStorePartialFailure:
    """FR-014: Handle partial scan failure with per-org status reporting."""

    def test_partial_scan_status_preserved(self, store):
        store.save_results_batch([
            DiscoveryResult(organization_id="org-a", repository_id="r1", repository_name="R1",
                            scan_status=ScanStatus.COMPLETED.value),
            DiscoveryResult(organization_id="org-b", repository_id="r2", repository_name="R2",
                            scan_status=ScanStatus.FAILED.value),
        ])

        summary = store.get_scan_summary()
        assert summary["by_status"]["completed"] == 1
        assert summary["by_status"]["failed"] == 1
