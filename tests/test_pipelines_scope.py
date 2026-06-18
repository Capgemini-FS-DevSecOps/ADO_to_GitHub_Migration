"""Tests for pipeline scope transform + GitHub push."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.scopes.pipelines_scope import PipelinesScopeHandler
from ado2gh.models import PipelineMetadata, PipelineType, RepoConfig


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="azure-pipelines",
        ado_repo="azure-pipelines-script-migration",
        gh_org="gh-org",
        gh_repo="azure-pipelines-script-migration",
        phase="poc",
        scopes=["pipelines"],
    )


def _pipe() -> PipelineMetadata:
    return PipelineMetadata(
        project="azure-pipelines",
        pipeline_id=42,
        pipeline_name="azure-pipelines-script-migration",
        pipeline_type=PipelineType.YAML,
        repo_name="azure-pipelines-script-migration",
    )


def test_pipelines_scope_fails_when_no_inventory(tmp_path):
    db = MagicMock()
    db.get_pipelines_for_repo.return_value = []
    db.get_wave_pipeline_migrations.return_value = []
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=MagicMock(), db=db)

    result = PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    assert result.failed == 1
    assert "No ADO pipelines" in result.stats["message"]


@patch("ado2gh.core.scopes.pipelines_scope.push_repo_workflows")
@patch.object(PipelinesScopeHandler, "_do_transform")
def test_pipelines_scope_pushes_after_transform(mock_transform, mock_push, tmp_path):
    mock_transform.return_value = {"pipeline_id": 42, "status": "completed"}
    mock_push.return_value = {
        "pushed": True,
        "workflow_files": ["ci.yml"],
        "pr_url": "https://github.com/gh-org/repo/pull/1",
        "workflow_branch": "ado2gh/migrated-workflows",
    }

    db = MagicMock()
    db.get_pipelines_for_repo.return_value = [_pipe()]
    db.get_wave_pipeline_migrations.return_value = []
    gh = MagicMock()
    ctx = ScopeContext(
        global_cfg={"workflow_branch": "ado2gh/migrated-workflows"},
        ado=MagicMock(),
        gh=gh,
        db=db,
        dry_run=False,
    )

    result = PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    assert result.failed == 0
    assert result.stats["workflows_pushed"] is True
    assert "ado2gh/migrated-workflows" in result.stats["message"]
    mock_push.assert_called_once()


@patch("ado2gh.core.scopes.pipelines_scope.push_repo_workflows")
@patch.object(PipelinesScopeHandler, "_do_transform")
def test_pipelines_scope_fails_when_push_fails(mock_transform, mock_push):
    mock_transform.return_value = {"pipeline_id": 42, "status": "completed"}
    mock_push.return_value = {
        "pushed": False,
        "error": "destination repo is empty",
        "workflow_files": ["ci.yml"],
    }

    db = MagicMock()
    db.get_pipelines_for_repo.return_value = [_pipe()]
    db.get_wave_pipeline_migrations.return_value = []
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=MagicMock(), db=db, dry_run=False)

    result = PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    assert result.failed >= 1
    assert "did not push" in result.stats["message"]


@patch("ado2gh.core.scopes.pipelines_scope.remote_workflow_files")
@patch("ado2gh.core.scopes.pipelines_scope.push_repo_workflows")
@patch.object(PipelinesScopeHandler, "_do_transform")
def test_pipelines_scope_retransforms_when_db_complete_but_github_missing(
    mock_transform, mock_push, mock_remote,
):
    mock_remote.return_value = []
    mock_push.return_value = {
        "pushed": False,
        "error": "no local workflows at /tmp",
        "workflow_files": [],
    }
    mock_transform.return_value = {"pipeline_id": 42, "status": "completed"}

    db = MagicMock()
    db.get_pipelines_for_repo.return_value = [_pipe()]
    db.get_wave_pipeline_migrations.return_value = [
        {
            "pipeline_id": 42,
            "status": "completed",
            "project": "azure-pipelines",
            "gh_org": "gh-org",
            "gh_repo": "azure-pipelines-script-migration",
        },
    ]
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=MagicMock(), db=db, dry_run=False)

    with patch(
        "ado2gh.core.scopes.pipelines_scope._finish_with_push",
    ) as mock_finish:
        mock_finish.return_value = MagicMock(failed=0, stats={"message": "pushed"})
        result = PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    mock_transform.assert_called_once()
    mock_finish.assert_called_once()
    assert result.failed == 0


@patch("ado2gh.core.scopes.pipelines_scope.remote_workflow_files")
@patch("ado2gh.core.scopes.pipelines_scope.push_repo_workflows")
@patch.object(PipelinesScopeHandler, "_do_transform")
def test_pipelines_scope_skips_when_remote_workflows_exist(
    mock_transform, mock_push, mock_remote,
):
    mock_remote.return_value = ["ci.yml"]
    db = MagicMock()
    db.get_pipelines_for_repo.return_value = [_pipe()]
    db.get_wave_pipeline_migrations.return_value = [
        {
            "pipeline_id": 42,
            "status": "completed",
            "project": "azure-pipelines",
            "gh_org": "gh-org",
            "gh_repo": "azure-pipelines-script-migration",
        },
    ]
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=MagicMock(), db=db, dry_run=False)

    result = PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    mock_transform.assert_not_called()
    mock_push.assert_not_called()
    assert result.failed == 0
    assert "already transformed" in result.stats["message"]
    assert result.stats["workflows_pushed"] is True


@patch("ado2gh.core.scopes.pipelines_scope.push_repo_workflows")
@patch.object(PipelinesScopeHandler, "_do_transform")
def test_pipelines_scope_does_not_skip_other_repo_completions(mock_transform, mock_push):
    mock_transform.return_value = {"pipeline_id": 42, "status": "completed"}
    mock_push.return_value = {
        "pushed": True,
        "workflow_files": ["ci.yml"],
        "pr_url": "https://github.com/gh-org/repo/pull/1",
    }

    db = MagicMock()
    db.get_pipelines_for_repo.return_value = [_pipe()]
    db.get_wave_pipeline_migrations.return_value = [
        {
            "pipeline_id": 99,
            "status": "completed",
            "project": "azure-pipelines",
            "gh_org": "gh-org",
            "gh_repo": "other-repo",
        },
    ]
    ctx = ScopeContext(global_cfg={}, ado=MagicMock(), gh=MagicMock(), db=db, dry_run=False)

    with patch("ado2gh.core.scopes.pipelines_scope.remote_workflow_files", return_value=[]):
        result = PipelinesScopeHandler().migrate(_repo(), ctx, wave_id=1)

    mock_transform.assert_called_once()
    assert result.failed == 0
