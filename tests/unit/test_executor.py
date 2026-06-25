"""Unit tests for executor (spec 011).
NOTE: Legacy executor deleted (spec 012) — rewrite for migration_agent executor node.
"""
import pytest

pytest.skip("Legacy executor deleted (spec 012) — rewrite for migration_agent", allow_module_level=True)
from ado2gh.agents.executor import AgentExecutor, ExecutionResult
from ado2gh.agents.planner import AgentPlanner


class TestExecutionResult:
    """T035: ExecutionResult structure tests."""

    def test_execution_result_has_required_fields(self):
        r = ExecutionResult()
        d = r.to_dict()
        assert "repo_mirror_status" in d
        assert "workflows_created" in d
        assert "secrets_provisioned" in d
        assert "issues_created" in d
        assert "wiki_enabled" in d
        assert "milestones_created" in d
        assert "packages_published" in d
        assert "failures" in d
        assert "skipped" in d
        assert "gaps" in d
        assert "guardrail_decisions" in d
        assert "rollback_records" in d

    def test_execution_result_defaults(self):
        r = ExecutionResult()
        assert r.repo_mirror_status == ""
        assert r.workflows_created == []
        assert r.secrets_provisioned == []
        assert r.issues_created == 0
        assert r.wiki_enabled is False
        assert r.failures == []
        assert r.skipped == []


class TestExecutePlan:
    """T035: Executor plan execution tests."""

    def _make_plan(self):
        planner = AgentPlanner()
        return planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data={
                "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 2}],
                "pipeline_inventory": [{"name": "build-ci", "repo": "Project/RepoA"}],
                "service_connections": [{"name": "azure-sub", "type": "azurerm"}],
            },
        )

    def test_execute_plan_returns_execution_result(self):
        executor = AgentExecutor()
        result = executor.execute_plan(self._make_plan(), dry_run=True)
        assert isinstance(result, ExecutionResult)

    def test_execute_plan_dry_run_repo_status(self):
        executor = AgentExecutor()
        result = executor.execute_plan(self._make_plan(), dry_run=True)
        assert result.repo_mirror_status == "success"

    def test_execute_plan_creates_workflows(self):
        executor = AgentExecutor()
        result = executor.execute_plan(self._make_plan(), dry_run=True)
        assert len(result.workflows_created) == 1
        assert result.workflows_created[0]["ado_pipeline"] == "build-ci"

    def test_execute_plan_provisions_secrets(self):
        executor = AgentExecutor()
        result = executor.execute_plan(self._make_plan(), dry_run=True)
        assert len(result.secrets_provisioned) == 1
        assert result.secrets_provisioned[0]["source"] == "azure-sub"

    def test_execute_plan_skips_blocked_repos(self):
        planner = AgentPlanner()
        plan = planner.plan(
            profile_id="lightweight",
            assignment_repos=["Project/RepoA"],
            dependency_edges=[],
            dry_run=True,
            discovery_data={
                "repos": [{"project": "Project", "repo_name": "RepoA", "total_score": 9}],
            },
        )
        executor = AgentExecutor()
        result = executor.execute_plan(plan, dry_run=True)
        assert len(result.skipped) == 1
        assert result.skipped[0]["repo"] == "Project/RepoA"

    def test_execute_plan_to_dict(self):
        executor = AgentExecutor()
        result = executor.execute_plan(self._make_plan(), dry_run=True)
        d = result.to_dict()
        assert d["repo_mirror_status"] == "success"
        assert len(d["workflows_created"]) == 1


class TestExecutorGuardrailIntegration:
    """T035: Executor guardrail integration tests."""

    def test_invoke_blocks_on_guardrail(self):
        executor = AgentExecutor()
        # No approved plan → should be blocked by guardrail
        result = executor.invoke(
            "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA"},
            approver_ok=True,
            approved_plan=None,
        )
        assert "error" in result
        assert result["error"] == "guardrail_blocked"

    def test_invoke_allows_with_plan(self):
        def mock_tool(args):
            return {"status": "ok"}
        executor = AgentExecutor(tool_registry={"ado2gh_enqueue_job": mock_tool})
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        result = executor.invoke(
            "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA"},
            approver_ok=True,
            approved_plan=plan,
        )
        assert "result" in result


class TestExecutorClarification:
    """T043: Executor clarification request tests."""

    def test_request_clarification_to_planner(self):
        executor = AgentExecutor()
        req = executor.request_clarification("pipeline_template", {"repo": "Project/RepoA"})
        assert req["type"] == "clarification_request"
        assert req["from"] == "executor"
        assert req["to"] == "planner"
        assert req["missing_info"] == "pipeline_template"


class TestIdempotencyDetection:
    """T044a: Idempotency detect-and-prompt tests."""

    def test_detect_already_migrated(self):
        executor = AgentExecutor()
        result = executor.detect_idempotency(
            "Project/RepoA",
            "my-org",
            existing_repos=["Project/RepoA", "Project/RepoB"],
        )
        assert result is not None
        assert result["type"] == "idempotency_detected"
        assert "overwrite" in result["options"]
        assert "skip" in result["options"]
        assert "abort" in result["options"]

    def test_detect_not_migrated(self):
        executor = AgentExecutor()
        result = executor.detect_idempotency(
            "Project/RepoC",
            "my-org",
            existing_repos=["Project/RepoA"],
        )
        assert result is None
