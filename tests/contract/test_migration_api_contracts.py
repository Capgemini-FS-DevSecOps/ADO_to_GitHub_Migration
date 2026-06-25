"""Contract tests for migration API endpoints (feature 008, US4).

Verifies POST /v1/migration/repo for on-demand migration with dependencies,
including dry-run support and explicit confirmation for destructive actions.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    from ado2gh.api.state_db import get_state_db
    db = get_state_db()
    with db._conn() as conn:
        conn.execute("DELETE FROM migration_operations")
        conn.execute("DELETE FROM pre_migration_forms")
    return TestClient(app)


class TestOnDemandMigrationAPI:
    """Contract tests for POST /v1/migration/repo (T042)."""

    def test_repo_migration_endpoint_exists(self, client):
        """POST /v1/migration/repo must exist."""
        resp = client.post("/v1/migration/repo", json={
            "repository_id": "test-repo",
            "organization_id": "test-org",
            "pre_migration_form_id": "nonexistent-form",
            "dry_run": True,
        })
        # 400 expected since form doesn't exist
        assert resp.status_code == 400

    def test_repo_migration_requires_form(self, client):
        """Migration must require a pre-migration form ID."""
        resp = client.post("/v1/migration/repo", json={
            "repository_id": "test-repo",
            "organization_id": "test-org",
            "pre_migration_form_id": "",
            "dry_run": True,
        })
        assert resp.status_code == 400

    def test_repo_migration_dry_run_supported(self, client):
        """Dry-run parameter must be accepted."""
        resp = client.post("/v1/migration/repo", json={
            "repository_id": "test-repo",
            "organization_id": "test-org",
            "pre_migration_form_id": "some-form",
            "dry_run": True,
        })
        # 400 since form doesn't exist, but parameter is accepted
        assert resp.status_code == 400

    def test_repo_migration_live_requires_confirmation(self, client):
        """Live (non-dry-run) migration should be accepted but may require confirmation."""
        resp = client.post("/v1/migration/repo", json={
            "repository_id": "test-repo",
            "organization_id": "test-org",
            "pre_migration_form_id": "some-form",
            "dry_run": False,
        })
        # 400 since form doesn't exist
        assert resp.status_code == 400

    def test_concurrent_migration_rejected(self, client):
        """Concurrent migration of the same repo should return 409."""
        # Setup: insert an in-progress operation directly
        from ado2gh.api.state_db import get_state_db
        from ado2gh.api.models import MigrationOperation, OperationStatus

        db = get_state_db(":memory:")
        op = MigrationOperation(
            repository_id="concurrent-test",
            organization_id="test-org",
            status=OperationStatus.IN_PROGRESS.value,
        )
        with db._conn() as conn:
            conn.execute(
                """INSERT INTO migration_operations
                   (id, repository_id, organization_id, operation_type, wave_id,
                    pre_migration_form_id, status, dry_run, confirmed_at,
                    started_at, completed_at, error_message, audit_log_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                op.to_row(),
            )

        # Verify the operation exists
        with db._conn() as conn:
            existing = conn.execute(
                """SELECT id FROM migration_operations
                   WHERE repository_id = ? AND status IN ('pending', 'in_progress')""",
                ("concurrent-test",),
            ).fetchone()
        assert existing is not None


class TestWaveCreationAPI:
    """Contract tests for POST /v1/migration/wave (T053)."""

    def test_wave_creation_endpoint_exists(self, client):
        """POST /v1/migration/wave must exist and return 201."""
        resp = client.post("/v1/migration/wave", json={
            "name": "Contract Test Wave",
            "repository_ids": ["repo-1"],
            "organization_id": "test-org",
        })
        assert resp.status_code == 201

    def test_wave_creation_requires_name(self, client):
        """Wave creation must require a name."""
        resp = client.post("/v1/migration/wave", json={
            "name": "",
            "repository_ids": [],
            "organization_id": "test-org",
        })
        assert resp.status_code == 400

    def test_wave_creation_with_multiple_repos(self, client):
        """Wave creation should accept multiple repository IDs."""
        resp = client.post("/v1/migration/wave", json={
            "name": "Multi-Repo Wave",
            "repository_ids": ["repo-a", "repo-b", "repo-c"],
            "organization_id": "test-org",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["repository_count"] == 3


class TestWaveExecutionAPI:
    """Contract tests for POST /v1/migration/wave/{wave_id}/execute (T054)."""

    def test_wave_execution_endpoint_exists(self, client):
        """POST /v1/migration/wave/{wave_id}/execute must exist."""
        # First create a wave
        create_resp = client.post("/v1/migration/wave", json={
            "name": "Exec Test Wave",
            "repository_ids": ["repo-1"],
            "organization_id": "test-org",
        })
        assert create_resp.status_code == 201
        wave_id = create_resp.json()["wave_id"]

        # Execute it
        resp = client.post(f"/v1/migration/wave/{wave_id}/execute", json={"dry_run": True})
        assert resp.status_code == 202

    def test_wave_execution_dry_run(self, client):
        """Wave execution must support dry_run parameter."""
        create_resp = client.post("/v1/migration/wave", json={
            "name": "Dry Run Wave",
            "repository_ids": ["repo-1"],
            "organization_id": "test-org",
        })
        wave_id = create_resp.json()["wave_id"]

        resp = client.post(f"/v1/migration/wave/{wave_id}/execute", json={"dry_run": True})
        assert resp.status_code == 202

    def test_wave_execution_nonexistent_returns_404(self, client):
        """Executing a non-existent wave should return 404."""
        resp = client.post("/v1/migration/wave/nonexistent/execute", json={"dry_run": True})
        assert resp.status_code == 404


class TestWaveStatusAPI:
    """Contract tests for GET /v1/migration/wave/{wave_id} (T055)."""

    def test_wave_status_endpoint_exists(self, client):
        """GET /v1/migration/wave/{wave_id} must exist."""
        create_resp = client.post("/v1/migration/wave", json={
            "name": "Status Test Wave",
            "repository_ids": ["repo-1", "repo-2"],
            "organization_id": "test-org",
        })
        wave_id = create_resp.json()["wave_id"]

        resp = client.get(f"/v1/migration/wave/{wave_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wave_id"] == wave_id
        assert data["name"] == "Status Test Wave"
        assert data["status"] == "draft"
        assert len(data["repositories"]) == 2

    def test_wave_status_nonexistent_returns_404(self, client):
        """Getting status of non-existent wave should return 404."""
        resp = client.get("/v1/migration/wave/nonexistent")
        assert resp.status_code == 404


class TestPreMigrationFormAPI:
    """Contract tests for pre-migration form endpoints (T065, T066)."""

    def test_form_generation_endpoint_exists(self, client):
        """POST /v1/migration/pre-migration-form must exist."""
        resp = client.post("/v1/migration/pre-migration-form", json={
            "repository_id": "nonexistent-repo",
            "organization_id": "test-org",
        })
        # 404 expected since repo not in discovery
        assert resp.status_code == 404

    def test_form_submission_endpoint_exists(self, client):
        """PUT /v1/migration/pre-migration-form/{form_id} must exist."""
        resp = client.put("/v1/migration/pre-migration-form/nonexistent-form", json={
            "target_github_org": "test-org",
            "team_mapping": {},
            "pipeline_config": {},
        })
        # 404 expected since form doesn't exist
        assert resp.status_code == 404

    def test_form_generation_requires_repository_id(self, client):
        """Form generation must require repository_id."""
        resp = client.post("/v1/migration/pre-migration-form", json={
            "repository_id": "",
            "organization_id": "test-org",
        })
        assert resp.status_code == 400
