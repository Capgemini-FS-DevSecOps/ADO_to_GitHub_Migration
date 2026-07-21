"""Unit tests for WorkflowValidator (feature 009, T037 + T038)."""
from __future__ import annotations

from unittest.mock import patch

from ado2gh.pipelines.validation.workflow_validator import (
    INVALID,
    MODE_ACTIONLINT,
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


class TestWorkflowValidatorWithActionlint:
    """Tests for T037: Unit test for WorkflowValidator with actionlint."""

    @patch("shutil.which")
    def test_actionlint_mode_when_actionlint_available(self, mock_which):
        """Verify actionlint mode is used when actionlint is on PATH."""
        mock_which.return_value = "/usr/bin/actionlint"
        validator = WorkflowValidator()
        assert validator.has_actionlint is True

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_subprocess_call_to_actionlint(self, mock_run, mock_which):
        """Verify subprocess call to actionlint with workflow file."""
        mock_which.return_value = "/usr/bin/actionlint"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        validator = WorkflowValidator()
        # Create a temporary file for testing
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(VALID_WORKFLOW)
            f.flush()
            result = validator.validate(f.name)

        assert result.validation_status == VALID
        assert result.validation_mode == MODE_ACTIONLINT
        mock_run.assert_called_once()
        # Verify actionlint was called with the file path
        call_args = mock_run.call_args[0]
        assert "actionlint" in call_args[0]

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_parse_actionlint_output_on_error(self, mock_run, mock_which):
        """Verify actionlint output is parsed on validation error."""
        mock_which.return_value = "/usr/bin/actionlint"
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "test.yml:3: missing 'runs-on'"

        validator = WorkflowValidator()
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(INVALID_WORKFLOW)
            f.flush()
            result = validator.validate(f.name)

        assert result.validation_status == INVALID
        assert any("runs-on" in e for e in result.validation_errors)


class TestWorkflowValidatorYamlFallback:
    """Tests for T038: Unit test for WorkflowValidator YAML fallback."""

    @patch("shutil.which")
    def test_yaml_fallback_when_actionlint_unavailable(self, mock_which):
        """Verify YAML fallback mode when actionlint not on PATH."""
        mock_which.return_value = None
        validator = WorkflowValidator()
        assert validator.has_actionlint is False
        assert validator.validation_mode == MODE_YAML_FALLBACK

    @patch("shutil.which")
    def test_validation_mode_actionlint_when_available(self, mock_which):
        mock_which.return_value = "/usr/bin/actionlint"
        validator = WorkflowValidator()
        assert validator.validation_mode == MODE_ACTIONLINT

    @patch("shutil.which")
    def test_yaml_parse_valid_workflow(self, mock_which):
        """Verify YAML parse + structural checks for valid workflow."""
        mock_which.return_value = None
        validator = WorkflowValidator()
        result = validator.validate_content(VALID_WORKFLOW)
        assert result.validation_status == VALID
        assert result.validation_mode == MODE_YAML_FALLBACK
        assert result.validation_errors == []

    @patch("shutil.which")
    def test_yaml_parse_missing_runs_on(self, mock_which):
        """Verify YAML parse detects missing runs-on."""
        mock_which.return_value = None
        validator = WorkflowValidator()
        result = validator.validate_content(INVALID_WORKFLOW)
        assert result.validation_status == INVALID
        assert any("runs-on" in e for e in result.validation_errors)

    @patch("shutil.which")
    def test_yaml_parse_missing_steps(self, mock_which):
        """Verify YAML parse detects missing steps."""
        mock_which.return_value = None
        validator = WorkflowValidator()
        result = validator.validate_content(INVALID_WORKFLOW)
        assert result.validation_status == INVALID
        assert any("steps" in e for e in result.validation_errors)

    @patch("shutil.which")
    def test_yaml_parse_malformed_yaml(self, mock_which):
        """Verify YAML parse detects malformed YAML."""
        mock_which.return_value = None
        validator = WorkflowValidator()
        result = validator.validate_content("jobs: [unclosed")
        assert result.validation_status == INVALID
        assert any("YAML" in e or "parse" in e for e in result.validation_errors)

    @patch("shutil.which")
    def test_secret_refs_detection(self, mock_which):
        """Verify secret references are collected in YAML fallback."""
        mock_which.return_value = None
        validator = WorkflowValidator()
        result = validator.validate_content(VALID_WORKFLOW)
        assert "MY_TOKEN" in result.secret_refs


class TestWorkflowCommitConflictHandling:
    """Tests for T039: Unit test for workflow commit conflict handling."""

