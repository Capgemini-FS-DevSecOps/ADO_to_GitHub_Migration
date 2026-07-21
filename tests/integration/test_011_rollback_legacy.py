"""Integration test for rollback on cancellation (spec 011).

T073: Tests that rollback tracking works during migration and that
cancel with rollback initiates resource cleanup.
NOTE: Legacy executor/planner deleted (spec 012) — rewrite for migration_agent.
"""
import pytest

pytest.skip("Legacy executor/planner deleted (spec 012) — rewrite for migration_agent", allow_module_level=True)
from ado2gh.agents.rollback_tracker import RollbackTracker
from ado2gh.agents.executor import AgentExecutor
from ado2gh.agents.planner import AgentPlanner
import uuid


class TestRollbackOnCancellation:
    """T073: Rollback tracking and cancellation tests."""

    def test_rollback_tracker_records_creations(self):
        """RollbackTracker records created GitHub resources for later cleanup."""
        tracker = RollbackTracker()
        sid = f"rollback-test-{uuid.uuid4()}"
        tracker.record_creation(
            session_id=sid,
            resource_type="workflow",
            resource_name=".github/workflows/ci.yml",
            github_org="my-org",
            correlation_id="repo-1",
        )
        tracker.record_creation(
            session_id=sid,
            resource_type="secret",
            resource_name="AZURE_SUBSCRIPTION",
            github_org="my-org",
            correlation_id="repo-1",
        )
        records = tracker.get_eligible(sid)
        assert len(records) == 2
        assert any(r["resource_type"] == "workflow" for r in records)
        assert any(r["resource_type"] == "secret" for r in records)

    def test_rollback_tracker_marks_completed(self):
        """RollbackTracker marks records as deleted after cleanup."""
        tracker = RollbackTracker()
        sid = f"rollback-test-{uuid.uuid4()}"
        rid = tracker.record_creation(
            session_id=sid,
            resource_type="workflow",
            resource_name=".github/workflows/ci.yml",
            github_org="my-org",
            correlation_id="repo-1",
        )
        assert tracker.mark_deleted(rid) is True
        eligible = tracker.get_eligible(sid)
        assert len(eligible) == 0

    def test_executor_tracks_rollback_records(self):
        """Executor records rollback entries for created resources."""
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data={
                "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
                "pipeline_inventory": [{"name": "build-ci", "repo": "Project/RepoA"}],
            },
        )
        executor = AgentExecutor()
        result = executor.execute_plan(plan, dry_run=True, session_id="rollback-test-3")
        # In dry-run, rollback_records may be empty but the field exists
        assert hasattr(result, "rollback_records")
        assert isinstance(result.rollback_records, list)

    def test_cancel_with_rollback_via_api(self):
        """Cancel endpoint with rollback action returns rollback initiated."""
        from fastapi.testclient import TestClient
        from services.agent.main import app

        client = TestClient(app)
        # Create a session
        resp = client.post("/v1/sessions", json={"message": "test rollback"})
        assert resp.status_code in (200, 201)
        sid = resp.json().get("session_id")
        if sid:
            cancel_resp = client.post(
                f"/v1/sessions/{sid}/cancel",
                json={"action": "rollback"},
            )
            assert cancel_resp.status_code == 200
            data = cancel_resp.json()
            assert data["status"] == "cancelled"
            assert data["rollback"] == "initiated"

    def test_cancel_without_rollback_via_api(self):
        """Cancel endpoint with stop action does not initiate rollback."""
        from fastapi.testclient import TestClient
        from services.agent.main import app

        client = TestClient(app)
        resp = client.post("/v1/sessions", json={"message": "test stop"})
        assert resp.status_code in (200, 201)
        sid = resp.json().get("session_id")
        if sid:
            cancel_resp = client.post(
                f"/v1/sessions/{sid}/cancel",
                json={"action": "stop"},
            )
            assert cancel_resp.status_code == 200
            data = cancel_resp.json()
            assert data["status"] == "cancelled"
            assert data["rollback"] == "not_requested"
