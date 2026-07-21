"""Tests for core migrate feature routes (git-mirror, pipeline-convert)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.core.scopes.base import ScopeResult
from services.accelerator_api.routes import migrate_routes


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(migrate_routes.router)
    return TestClient(app)


def _mock_clients():
    return MagicMock(), MagicMock(), "test-org", MagicMock()


@patch.object(migrate_routes, "_get_clients", side_effect=lambda: _mock_clients())
@patch.object(migrate_routes, "_load_global_cfg", return_value={"migration_strategy": "mirror"})
@patch.object(migrate_routes, "_state_db", return_value=MagicMock())
def test_git_mirror_dry_run(mock_db, mock_cfg, mock_clients, client):
  with patch("ado2gh.core.scopes.git_scope.GitScopeHandler") as handler_cls:
    handler_cls.return_value.migrate.return_value = ScopeResult(
      stats={"dry_run": True, "strategy": "mirror", "branches": 3},
      failed=0,
    )
    resp = client.post(
      "/v1/migrate/git-mirror",
      json={
        "project": "MyProject",
        "repo_name": "my-repo",
        "github_org": "test-org",
        "github_repo": "my-repo",
        "dry_run": True,
      },
    )

  assert resp.status_code == 200
  body = resp.json()
  assert body["status"] == "dry_run"
  assert body["project"] == "MyProject"
  assert body["branches"] == 3


@patch.object(migrate_routes, "_get_clients", side_effect=lambda: _mock_clients())
@patch.object(migrate_routes, "_load_global_cfg", return_value={"migration_strategy": "gei"})
@patch.object(migrate_routes, "_state_db", return_value=MagicMock())
def test_git_mirror_defaults_github_org_from_profile(mock_db, mock_cfg, mock_clients, client):
  with patch("ado2gh.core.scopes.git_scope.GitScopeHandler") as handler_cls:
    handler_cls.return_value.migrate.return_value = ScopeResult(
      stats={"gei": "success"},
      failed=0,
    )
    resp = client.post(
      "/v1/migrate/git-mirror",
      json={
        "project": "MyProject",
        "repo_name": "my-repo",
        "dry_run": False,
      },
    )

  assert resp.status_code == 200
  body = resp.json()
  assert body["github_org"] == "test-org"
  assert body["github_repo"] == "my-repo"
  assert body["strategy"] == "gei"


@patch.object(migrate_routes, "_get_clients", side_effect=lambda: _mock_clients())
@patch.object(migrate_routes, "_load_global_cfg", return_value={})
@patch.object(migrate_routes, "_state_db", return_value=MagicMock())
def test_pipeline_convert_dry_run(mock_db, mock_cfg, mock_clients, client):
  with patch("ado2gh.core.scopes.pipelines_scope.PipelinesScopeHandler") as handler_cls:
    handler_cls.return_value.migrate.return_value = ScopeResult(
      stats={"dry_run": True, "total": 2, "completed": 2, "message": "ok"},
      failed=0,
    )
    resp = client.post(
      "/v1/migrate/pipeline-convert",
      json={"repo": "MyProject/my-repo", "dry_run": True},
    )

  assert resp.status_code == 200
  body = resp.json()
  assert body["status"] == "dry_run"
  assert body["repo"] == "MyProject/my-repo"
  assert body["github_org"] == "test-org"
  assert body["github_repo"] == "my-repo"


def test_parse_repo_key_rejects_invalid():
  with pytest.raises(Exception) as exc:
    migrate_routes._parse_repo_key("no-slash")
  assert "project/repo_name" in str(exc.value.detail)


@patch.object(migrate_routes, "_get_clients", side_effect=lambda: _mock_clients())
def test_git_mirror_route_registered(mock_clients, client):
  with patch("ado2gh.core.scopes.git_scope.GitScopeHandler") as handler_cls:
    handler_cls.return_value.migrate.return_value = ScopeResult(stats={}, failed=0)
    with patch.object(migrate_routes, "_load_global_cfg", return_value={}):
      with patch.object(migrate_routes, "_state_db", return_value=MagicMock()):
        resp = client.post(
          "/v1/migrate/git-mirror",
          json={
            "project": "P",
            "repo_name": "r",
            "github_org": "o",
            "github_repo": "r",
            "dry_run": True,
          },
        )
  assert resp.status_code == 200
