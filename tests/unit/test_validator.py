"""Unit tests for validator (spec 011).
NOTE: Legacy validator deleted (spec 012) — rewrite for migration_agent validator node.
"""
import pytest

pytest.skip("Legacy validator deleted (spec 012) — rewrite for migration_agent", allow_module_level=True)
from ado2gh.agents.validator import (
    AgentValidator,
    ValidationCheck,
    ValidationResult,
    ALL_SCOPES,
)
from ado2gh.agents.planner import AgentPlanner
from ado2gh.agents.executor import AgentExecutor


class TestValidationResult:
    """T045: ValidationResult structure tests."""

    def test_validation_result_has_required_fields(self):
        r = ValidationResult()
        d = r.to_dict()
        assert "passed" in d
        assert "scope_results" in d
        assert "checks" in d
        assert "failures" in d
        assert "feedback_to_planner" in d
        assert "evidence" in d

    def test_validation_result_defaults(self):
        r = ValidationResult()
        assert r.passed is False
        assert r.scope_results == {}
        assert r.checks == []
        assert r.failures == []


class TestValidateExecution:
    """T045: Validator execution validation tests."""

    def _make_plan_and_output(self):
        planner = AgentPlanner()
        plan = planner.plan(
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
        executor = AgentExecutor()
        exec_result = executor.execute_plan(plan, dry_run=True)
        return plan, exec_result.to_dict()

    def test_validate_execution_returns_validation_result(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        result = validator.validate_execution(plan, output, dry_run=True)
        assert isinstance(result, ValidationResult)

    def test_validate_execution_passes_for_valid_dry_run(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        result = validator.validate_execution(plan, output, dry_run=True)
        assert result.passed is True

    def test_validate_execution_checks_all_scopes(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        result = validator.validate_execution(plan, output, dry_run=True)
        for scope in ALL_SCOPES:
            assert scope in result.scope_results

    def test_validate_execution_builds_feedback_on_pass(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        result = validator.validate_execution(plan, output, dry_run=True)
        assert result.feedback_to_planner["type"] == "validation_passed"

    def test_validate_execution_builds_feedback_on_fail(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        # Simulate a failure: executor created workflow not in plan
        output["workflows_created"].append({
            "ado_pipeline": "evil-pipeline",
            "github_workflow": "evil.yml",
            "status": "created",
        })
        result = validator.validate_execution(plan, output, dry_run=True)
        assert result.passed is False
        assert result.feedback_to_planner["type"] == "validation_failed"
        assert "pipelines" in result.feedback_to_planner["failed_scopes"]

    def test_validate_execution_has_evidence(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        result = validator.validate_execution(plan, output, dry_run=True)
        assert len(result.evidence) > 0
        for evidence in result.evidence:
            assert "scope" in evidence
            assert "check_name" in evidence
            assert "passed" in evidence

    def test_validate_execution_to_dict(self):
        validator = AgentValidator()
        plan, output = self._make_plan_and_output()
        result = validator.validate_execution(plan, output, dry_run=True)
        d = result.to_dict()
        assert d["passed"] is True
        assert len(d["checks"]) > 0


class TestPlanConsistencyCheck:
    """T050: Plan-vs-execution consistency tests."""

    def test_unauthorized_workflow_detected(self):
        validator = AgentValidator()
        plan = {
            "work_items": [{"repo": "Project/RepoA"}],
            "pipeline_mappings": [{"github_workflow": "build.yml"}],
        }
        output = {
            "workflows_created": [
                {"github_workflow": "build.yml"},
                {"github_workflow": "evil.yml"},
            ],
        }
        result = validator.validate_execution(plan, output, dry_run=True)
        assert result.passed is False
        assert any("plan_consistency" in c.check_name and not c.passed for c in result.checks)

    def test_unauthorized_repo_operation_detected(self):
        validator = AgentValidator()
        plan = {
            "work_items": [{"repo": "Project/RepoA"}],
            "pipeline_mappings": [],
        }
        output = {
            "failures": [{"repo": "Project/RepoEVIL", "scope": "repo", "error": "test"}],
        }
        result = validator.validate_execution(plan, output, dry_run=True)
        assert result.passed is False
        assert any("plan_consistency_repo" in c.check_name and not c.passed for c in result.checks)


class TestPerScopeReporting:
    """T051: Per-scope pass/fail reporting tests."""

    def test_all_scopes_reported(self):
        validator = AgentValidator()
        plan = {"work_items": [{"repo": "Project/RepoA"}], "pipeline_mappings": []}
        output = {"workflows_created": [], "secrets_provisioned": []}
        result = validator.validate_execution(plan, output, dry_run=True)
        for scope in ALL_SCOPES:
            assert scope in result.scope_results

    def test_scope_results_are_booleans(self):
        validator = AgentValidator()
        plan = {"work_items": [{"repo": "Project/RepoA"}], "pipeline_mappings": []}
        output = {"workflows_created": [], "secrets_provisioned": []}
        result = validator.validate_execution(plan, output, dry_run=True)
        for scope, passed in result.scope_results.items():
            assert isinstance(passed, bool)
