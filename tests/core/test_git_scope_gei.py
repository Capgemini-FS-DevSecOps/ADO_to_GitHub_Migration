"""GEI (ado2gh) migration command tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.scopes.git_scope import GitScopeHandler
from ado2gh.models import RepoConfig


def test_run_gei_uses_ado2gh_extension():
    repo = RepoConfig(
        ado_project="proj",
        ado_repo="src",
        gh_org="gh-org",
        gh_repo="tgt",
        scopes=["repo"],
    )
    ctx = ScopeContext(
        global_cfg={"ado_org_url": "https://dev.azure.com/cloud-sre"},
        ado=MagicMock(pat="ado-pat"),
        gh=MagicMock(),
        db=MagicMock(),
        strategy="gei",
    )
    ctx.gh.token_manager.get_token.return_value = "gh-pat"
    handler = GitScopeHandler()

    with patch("ado2gh.core.scopes.git_scope.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="Migration completed",
            stderr="",
        )
        result = handler._run_gei(repo, {}, ctx)

    cmd = run.call_args[0][0]
    assert cmd[:3] == ["gh", "ado2gh", "migrate-repo"]
    env = run.call_args[1]["env"]
    assert env.get("DOTNET_SYSTEM_GLOBALIZATION_INVARIANT") == "1"
    assert "--ado-org" in cmd and "cloud-sre" in cmd
    assert result["gei"] == "success"


def test_run_gei_fails_when_target_repo_already_exists():
    repo = RepoConfig(
        ado_project="proj",
        ado_repo="src",
        gh_org="gh-org",
        gh_repo="tgt",
    )
    ctx = ScopeContext(
        global_cfg={"ado_org_url": "https://dev.azure.com/cloud-sre"},
        ado=MagicMock(pat="ado-pat"),
        gh=MagicMock(),
        db=MagicMock(),
        strategy="gei",
    )
    ctx.gh.token_manager.get_token.return_value = "gh-pat"
    ctx.gh.repo_exists.return_value = True
    ctx.ado.get_repo_commits.return_value = [{"commitId": "abc123"}]
    ctx.gh.list_branches.return_value = [
        {"name": "main", "commit": {"sha": "different"}},
    ]
    handler = GitScopeHandler()

    with patch("ado2gh.core.scopes.git_scope.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="No operation will be performed",
            stderr="",
        )
        try:
            handler._run_gei(repo, {"defaultBranch": "refs/heads/main", "id": "1"}, ctx)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "already exists" in str(exc).lower()


def test_migrate_gei_skips_when_target_repo_already_verified():
    repo = RepoConfig(
        ado_project="proj",
        ado_repo="src",
        gh_org="gh-org",
        gh_repo="tgt",
        scopes=["repo"],
    )
    source = {"defaultBranch": "refs/heads/main", "id": "1", "remoteUrl": "https://x", "size": 0}
    ctx = ScopeContext(
        global_cfg={"ado_org_url": "https://dev.azure.com/cloud-sre"},
        ado=MagicMock(pat="ado-pat"),
        gh=MagicMock(),
        db=MagicMock(),
        strategy="gei",
        dry_run=False,
    )
    ctx.ado.get_repo.return_value = source
    ctx.ado.get_repo_stats.return_value = {"branch_count": 1}
    ctx.ado.get_repo_commits.return_value = [{"commitId": "abc123def456"}]
    ctx.gh.repo_exists.return_value = True
    ctx.gh.list_branches.return_value = [
        {"name": "main", "commit": {"sha": "abc123def456"}},
    ]
    handler = GitScopeHandler()

    with patch("ado2gh.core.scopes.git_scope.subprocess.run") as run:
        result = handler.migrate(repo, ctx)

    run.assert_not_called()
    assert result.stats["gei"] == "skipped"
    assert result.stats["mirror"] == "already_migrated"
