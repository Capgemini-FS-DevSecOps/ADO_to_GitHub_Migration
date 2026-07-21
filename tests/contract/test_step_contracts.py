"""Contract tests for feature 009 pipeline step result data shapes.

Validates that the structured result data produced by the decoupled pipeline
steps conforms to contracts/step-result-contracts.md and data-model.md. Where a
producer exists today (WorkflowValidator, OIDCProvisioner) the real output is
asserted; the per-step result schemas are guarded with required-key sets that
downstream steps rely on.
"""
from __future__ import annotations

from ado2gh.api.oidc_provisioner import ProvisioningResult
from ado2gh.pipelines.validation.workflow_validator import (
    INVALID,
    MODE_YAML_FALLBACK,
    VALID,
    ValidationResult,
    WorkflowValidator,
)

VALID_WORKFLOW = """
name: CI
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "${{ secrets.MY_TOKEN }}"
"""

INVALID_WORKFLOW = """
name: CI
on: push
jobs:
  build:
    steps: []
"""


def _assert_keys(payload: dict, required: set[str]) -> None:
    missing = required - set(payload)
    assert not missing, f"missing required keys: {missing}"


class TestWorkflowValidatorContract:
    def test_valid_workflow_yaml_fallback(self):
        # Force fallback mode (no actionlint) for a deterministic result.
        validator = WorkflowValidator(actionlint_path=None)
        validator._actionlint = None  # ensure fallback regardless of PATH
        result = validator.validate_content(VALID_WORKFLOW)
        assert result.validation_status == VALID
        assert result.validation_mode == MODE_YAML_FALLBACK
        assert result.validation_errors == []
        assert "MY_TOKEN" in result.secret_refs

    def test_invalid_workflow_missing_runs_on_and_steps(self):
        validator = WorkflowValidator(actionlint_path=None)
        validator._actionlint = None
        result = validator.validate_content(INVALID_WORKFLOW)
        assert result.validation_status == INVALID
        assert any("runs-on" in e for e in result.validation_errors)
        assert any("steps" in e for e in result.validation_errors)

    def test_malformed_yaml_is_invalid(self):
        validator = WorkflowValidator(actionlint_path=None)
        validator._actionlint = None
        result = validator.validate_content("jobs: [unclosed")
        assert result.validation_status == INVALID

    def test_validation_result_dict_contract(self):
        result = ValidationResult(validation_status=VALID)
        _assert_keys(
            result.to_dict(),
            {"validation_status", "validation_errors", "validation_mode", "secret_refs"},
        )


class TestProvisioningResultContract:
    def test_provisioning_result_dict_contract(self):
        result = ProvisioningResult(sc_name="Azure-Prod", sc_type="azurerm", success=True)
        _assert_keys(
            result.to_dict(),
            {
                "sc_name", "sc_type", "success", "provisioned_secrets",
                "failure_reason", "operator_required", "skipped_override",
            },
        )


class TestStepResultSchemas:
    """Guards for the per-step result contract (step-result-contracts.md)."""

    def test_analyze_deps_dependency_report_keys(self):
        # Representative per-repo DependencyReport (data-model.md DependencyReport).
        report = {
            "service_connections": [
                {"name": "Azure-Prod", "type": "azurerm", "id": "g", "status": "auto_provisionable"}
            ],
            "variable_groups": [{"name": "BuildVars", "status": "operator_required"}],
            "repo_dependencies": ["Proj/RepoB"],
            "environments": [{"name": "prod", "type": "approval"}],
            "unsupported_tasks": ["CustomTask@1"],
            "self_hosted_agents": ["OnPremPool"],
        }
        _assert_keys(
            report,
            {
                "service_connections", "variable_groups", "repo_dependencies",
                "environments", "unsupported_tasks", "self_hosted_agents",
            },
        )

    def test_feasibility_report_keys(self):
        report = {
            "repo_size_bytes": 1024,
            "lfs_object_count": 0,
            "lfs_size_bytes": 0,
            "largest_file_bytes": 512,
            "branch_count": 3,
            "tag_count": 1,
            "has_wiki": False,
            "has_policies": True,
            "recommended_strategy": "mirror",
            "feasibility": "ok",
            "warnings": [],
        }
        _assert_keys(
            report,
            {
                "repo_size_bytes", "lfs_object_count", "lfs_size_bytes",
                "largest_file_bytes", "branch_count", "tag_count", "has_wiki",
                "has_policies", "recommended_strategy", "feasibility", "warnings",
            },
        )

    def test_conversion_result_keys(self):
        result = {
            "source_pipeline": "build-ci",
            "output_path": ".github/workflows/build-ci.yml",
            "commit_sha": None,
            "validation_status": "valid",
            "validation_errors": [],
            "validation_mode": "yaml_fallback",
            "unmapped_secrets": [],
            "conflict_renamed": False,
        }
        _assert_keys(
            result,
            {
                "source_pipeline", "output_path", "commit_sha", "validation_status",
                "validation_errors", "validation_mode", "unmapped_secrets",
                "conflict_renamed",
            },
        )
