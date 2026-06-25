"""Unit tests for guardrails (spec 011).
NOTE: Legacy guardrail API tests — tool names and decision format changed in spec 012.
See test_guardrails.py for current API tests.
"""
import pytest

pytest.skip("Legacy guardrail API tests (spec 011) — superseded by test_guardrails.py (spec 012)", allow_module_level=True)


class TestGuardrailAllow:
    """Tests for operations that should be allowed."""

    def test_readonly_tool_allowed(self):
        d = evaluate_guardrail("planner", "ado2gh_discover", {})
        assert d.decision == "allow"

    def test_executor_job_with_approved_plan(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA"},
            approved_plan=plan,
        )
        assert d.decision == "allow"

    def test_validator_can_validate(self):
        d = evaluate_guardrail("validator", "ado2gh_validate_repo", {})
        assert d.decision == "allow"


class TestGuardrailBlock:
    """Tests for operations that should be blocked."""

    def test_unknown_tool_blocked(self):
        d = evaluate_guardrail("executor", "nonexistent_tool", {})
        assert d.decision == "block"
        assert "Unknown tool" in d.reason

    def test_wrong_role_blocked(self):
        d = evaluate_guardrail("planner", "ado2gh_enqueue_job", {})
        assert d.decision == "block"
        assert "does not have access" in d.reason

    def test_delete_without_authorization_blocked(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"operation_type": "delete", "target_resource": "Project/RepoA"},
            approved_plan=plan,
        )
        assert d.decision == "block"
        assert "explicit authorization" in d.reason

    def test_delete_with_authorization_allowed(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {
                "operation_type": "delete",
                "target_resource": "Project/RepoA",
                "explicitly_authorized": True,
            },
            approved_plan=plan,
        )
        assert d.decision == "allow"

    def test_write_without_plan_blocked(self):
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA"},
            approved_plan=None,
        )
        assert d.decision == "block"
        assert "approved plan" in d.reason

    def test_target_not_in_plan_blocked(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoEVIL"},
            approved_plan=plan,
        )
        assert d.decision == "block"
        assert "not found in approved plan" in d.reason


class TestAdoReadOnlyEnforcement:
    """T034: ADO read-only enforcement tests."""

    def test_ado_migrate_boards_blocked_without_cleanup(self):
        d = evaluate_guardrail(
            "executor", "ado2gh_migrate_boards",
            {"operation_type": "create"},
        )
        assert d.decision == "block"
        assert "ADO write" in d.reason

    def test_ado_migrate_boards_allowed_with_cleanup(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_migrate_boards",
            {"operation_type": "create", "migration_cleanup_approved": True},
            approved_plan=plan,
        )
        assert d.decision == "allow"

    def test_ado_list_service_connections_allowed(self):
        d = evaluate_guardrail(
            "planner", "ado2gh_list_service_connections",
            {"operation_type": "list"},
        )
        assert d.decision == "allow"


class TestParameterValidation:
    """T032: Tool parameter validation tests."""

    def test_invalid_repo_id_format_blocked(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"repository_id": "RepoA", "target_resource": "Project/RepoA"},
            approved_plan=plan,
        )
        assert d.decision == "block"
        assert "Project/RepoName" in d.reason

    def test_path_traversal_blocked(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA", "path": "../../etc/passwd"},
            approved_plan=plan,
        )
        assert d.decision == "block"
        assert "invalid path" in d.reason.lower()

    def test_valid_parameters_allowed(self):
        plan = {"work_items": [{"repo": "Project/RepoA"}]}
        d = evaluate_guardrail(
            "executor", "ado2gh_enqueue_job",
            {"target_resource": "Project/RepoA", "repository_id": "Project/RepoA"},
            approved_plan=plan,
        )
        assert d.decision == "allow"


class TestRoleAccess:
    """Tests for role-based tool access."""

    def test_planner_cannot_access_executor_tools(self):
        assert can_access_tool("planner", "ado2gh_enqueue_job") is False

    def test_executor_cannot_access_planner_tools(self):
        assert can_access_tool("executor", "ado2gh_plan_phase") is False

    def test_tools_for_role_returns_correct_tools(self):
        planner_tools = tools_for_role("planner")
        tool_names = {t.name for t in planner_tools}
        assert "ado2gh_discover" in tool_names
        assert "ado2gh_enqueue_job" not in tool_names

    def test_executor_tools_for_role(self):
        executor_tools = tools_for_role("executor")
        tool_names = {t.name for t in executor_tools}
        assert "ado2gh_enqueue_job" in tool_names
        assert "ado2gh_migrate_boards" in tool_names
