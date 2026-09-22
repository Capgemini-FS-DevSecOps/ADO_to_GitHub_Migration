"""Regression check for register entry GAP-030: the GitHub credential must never reach subprocess argv.

The mirror strategy used to embed the GitHub credential in the push remote, so
the process table exposed it to any local user for the duration of the push. It
now travels in the git config environment, the way the Azure DevOps side has
always done it. The literals below are obvious placeholders, not credentials.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from ado2gh.core.scopes.base import ScopeContext
from ado2gh.core.scopes.git_scope import GitScopeHandler
from ado2gh.models import ExecutionMode, RepoConfig

PLACEHOLDER_GH = "placeholder-not-a-github-credential"
PLACEHOLDER_ADO = "placeholder-not-an-ado-credential"
TARGET_URL = "https://github.com/gh-org/tgt.git"
ADO_CLONE_URL = "https://cloud-sre@dev.azure.com/cloud-sre/proj/_git/src"


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="proj",
        ado_repo="src",
        gh_org="gh-org",
        gh_repo="tgt",
        scopes=["repo"],
    )


def _ctx(mode: ExecutionMode = ExecutionMode.LIVE) -> ScopeContext:
    ctx = ScopeContext(
        global_cfg={"ado_org_url": "https://dev.azure.com/cloud-sre"},
        ado=MagicMock(pat=PLACEHOLDER_ADO),
        gh=MagicMock(),
        db=MagicMock(),
        strategy="mirror",
        mode=mode,
    )
    ctx.gh.token_manager.get_token.return_value = PLACEHOLDER_GH
    return ctx


def _fake_run(cmd, **_kwargs):
    """Succeed for every git call, reporting one Git Large File Storage (LFS) object so the push runs."""
    stdout = "one-object.bin" if cmd[1:3] == ["lfs", "ls-files"] else ""
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _mirror_calls():
    handler = GitScopeHandler()
    with patch("ado2gh.core.scopes.git_scope.subprocess.run", side_effect=_fake_run) as run:
        handler._run_mirror(_repo(), ADO_CLONE_URL, _ctx())
    return run.call_args_list


def _argv(call) -> list[str]:
    return list(call.args[0])


def _git_config(call) -> dict[str, str]:
    """Read back the GIT_CONFIG_* overrides from a call's subprocess env."""
    env = call.kwargs.get("env") or {}
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)}


def _find(calls, *prefix):
    return next(c for c in calls if _argv(c)[1:1 + len(prefix)] == list(prefix))


def _basic(user: str, secret: str) -> str:
    return "Authorization: Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def test_no_argv_element_carries_a_credential():
    encoded_gh = base64.b64encode(f"x-access-token:{PLACEHOLDER_GH}".encode()).decode()
    for call in _mirror_calls():
        for arg in _argv(call):
            assert PLACEHOLDER_GH not in arg
            assert PLACEHOLDER_ADO not in arg
            assert encoded_gh not in arg


def test_push_remote_is_tokenless():
    calls = _mirror_calls()
    set_url = _find(calls, "remote", "set-url", "origin")
    assert _argv(set_url)[-1] == TARGET_URL


def test_push_env_carries_the_url_scoped_auth_header():
    calls = _mirror_calls()
    push = _find(calls, "push", "--force", "origin")
    config = _git_config(push)
    assert config[f"http.{TARGET_URL}.extraHeader"] == _basic("x-access-token", PLACEHOLDER_GH)
    assert (push.kwargs.get("env") or {})["GIT_TERMINAL_PROMPT"] == "0"


def test_lfs_push_is_tokenless_and_authenticated_through_env():
    calls = _mirror_calls()
    lfs_push = _find(calls, "lfs", "push", "--all")
    assert _argv(lfs_push)[-1] == TARGET_URL
    config = _git_config(lfs_push)
    assert config[f"http.{TARGET_URL}.extraHeader"] == _basic("x-access-token", PLACEHOLDER_GH)


def test_ado_clone_auth_is_unchanged():
    calls = _mirror_calls()
    clone = _find(calls, "clone", "--mirror")
    assert _argv(clone)[-2] == "https://dev.azure.com/cloud-sre/proj/_git/src"
    assert _git_config(clone)["http.extraHeader"] == _basic("", PLACEHOLDER_ADO)


def test_dry_run_starts_no_subprocess():
    """Nothing may execute until the operator has chosen live mode (CA-001)."""
    ctx = _ctx(ExecutionMode.DRY_RUN)
    ctx.ado.get_repo.return_value = {"remoteUrl": ADO_CLONE_URL, "size": 0, "id": "1"}
    ctx.ado.get_repo_stats.return_value = {"branch_count": 1}
    with patch("ado2gh.core.scopes.git_scope.subprocess.run") as run:
        result = GitScopeHandler().migrate(_repo(), ctx)
    run.assert_not_called()
    assert result.stats["dry_run"] is True
