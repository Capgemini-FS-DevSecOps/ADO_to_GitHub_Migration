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
    handler = GitScopeHandler()

    with patch("ado2gh.core.scopes.git_scope.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="No operation will be performed",
            stderr="",
        )
        try:
            handler._run_gei(repo, {}, ctx)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "already exists" in str(exc).lower()
