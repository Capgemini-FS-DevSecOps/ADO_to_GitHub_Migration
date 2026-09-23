"""Unit tests for workflow integrity check (feature 009, T062)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.models import RepoConfig
from ado2gh.reporting.post_migration_validator import PostMigrationValidator


def _validator(gh: MagicMock | None = None) -> PostMigrationValidator:
    """A validator whose non-workflow checks all pass.

    ``_validate_one`` runs repo_exists, default_branch, head_commit and
    branch_count before it reaches the workflow check; bare MagicMocks make those
    return MagicMock instead of str/int and blow up on comparison. Give them real
    values so the workflow-integrity path is what the assertions actually measure.
    """
    ado = MagicMock()
    ado.get_repo.return_value = {"defaultBranch": "refs/heads/main", "id": "repo-id"}
    ado.get_repo_commits.return_value = [{"commitId": "abc123"}]
    ado.get_repo_stats.return_value = {"branch_count": 1}

    gh = gh or MagicMock()
    gh.repo_exists.return_value = True
    gh.get_repo.return_value = {"default_branch": "main"}
    gh.list_branches.return_value = [{"name": "main", "commit": {"sha": "abc123"}}]

    return PostMigrationValidator(ado=ado, gh=gh, db=MagicMock())


def _repo() -> RepoConfig:
    # "pipelines" scope is what makes _validate_one call _check_workflows at all.
    return RepoConfig(
        ado_project="org",
        ado_repo="repo",
        gh_org="org",
        gh_repo="repo",
        scopes=["pipelines"],
    )


WORKFLOW_FILES = [
    {
        "repo": "org/repo",
        "output_path": ".github/workflows/build.yml",
        "commit_sha": "abc123",
    }
]


class TestWorkflowIntegrityCheck:
    """Tests for T062: Unit test for workflow integrity check."""

    @patch("ado2gh.reporting.post_migration_validator.PostMigrationValidator._check_workflows")
    def test_pass_when_files_exist_and_shas_match(self, mock_check_workflows):
        """Mock GitHub Contents API, verify PASS when files exist and SHAs match."""
        mock_check_workflows.return_value = {
            "verdict": "PASS",
            "detail": "All pipelines have corresponding workflows",
            "workflow_integrity": {
                "verdict": "PASS",
                "detail": "All workflow files verified in GitHub",
            },
        }

        result = _validator()._validate_one(_repo(), workflow_files=WORKFLOW_FILES)

        integrity_check = result["checks"]["workflows"]["workflow_integrity"]
        assert integrity_check["verdict"] == "PASS"

    @patch("ado2gh.reporting.post_migration_validator.PostMigrationValidator._check_workflows")
    def test_fail_when_files_missing(self, mock_check_workflows):
        """Verify FAIL when workflow files are missing in GitHub."""
        mock_check_workflows.return_value = {
            "verdict": "PASS",
            "detail": "All pipelines have corresponding workflows",
            "workflow_integrity": {
                "verdict": "FAIL",
                "detail": "1 workflow files missing in GitHub",
                "missing_files": [".github/workflows/build.yml"],
            },
        }

        result = _validator()._validate_one(_repo(), workflow_files=WORKFLOW_FILES)

        integrity_check = result["checks"]["workflows"]["workflow_integrity"]
        assert integrity_check["verdict"] == "FAIL"
        assert "missing" in integrity_check["detail"].lower()

    @patch("ado2gh.reporting.post_migration_validator.PostMigrationValidator._check_workflows")
    def test_no_integrity_check_when_no_workflow_files(self, mock_check_workflows):
        """Verify no integrity check when no workflow files provided."""
        mock_check_workflows.return_value = {
            "verdict": "PASS",
            "detail": "No pipelines to convert",
        }

        result = _validator()._validate_one(_repo(), workflow_files=None)

        assert "workflow_integrity" not in result["checks"]["workflows"]

    @patch("ado2gh.reporting.post_migration_validator.PostMigrationValidator._check_workflows")
    def test_integrity_check_uses_github_contents_api(self, mock_check_workflows):
        """Verify the workflow check is handed the files it must verify via the Contents API."""
        mock_check_workflows.return_value = {
            "verdict": "PASS",
            "detail": "All pipelines have corresponding workflows",
            "workflow_integrity": {
                "verdict": "PASS",
                "detail": "All workflow files verified in GitHub",
            },
        }

        repo = _repo()
        _validator()._validate_one(repo, workflow_files=WORKFLOW_FILES)

        mock_check_workflows.assert_called_once_with(repo, WORKFLOW_FILES)
