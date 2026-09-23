"""Guards around the irreversible archive step of `ado-cleanup` (COV-DRIFT-004).

Archiving disables the Azure DevOps repository so nothing can be pushed to it
again. Two guards stand in front of it and both are asserted here as *absence of
a call*, not as a returned value:

1. it is opt-in — `archive_repo` defaults to ``False``, so the CLI's `--archive`
   flag is the only way to reach it;
2. it is mode-gated — `ExecutionMode.DRY_RUN`, the default, returns the rehearsal
   marker without touching the repository (CA-001).

The third guard the drift report asked about — refusing to archive until a
commit-SHA validation has recorded a match — does not exist in the module today.
It is recorded as a follow-up rather than asserted here; no production code is
changed by this module.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ado2gh.core.ado_cleanup import ADOCleanup
from ado2gh.models import ExecutionMode, RepoConfig

REPO = RepoConfig(
    ado_project="Contoso",
    ado_repo="payments",
    gh_org="fake-gh-org",
    gh_repo="payments",
)


def _ado() -> MagicMock:
    ado = MagicMock()
    ado.org_url = "https://dev.azure.com/fake-org"
    ado.API = "api-version=7.1"
    ado._encode_project.side_effect = lambda project: project
    ado.list_all_pipelines.return_value = []
    ado.get_repo.return_value = {"id": "repo-guid", "defaultBranch": "refs/heads/main"}
    ado._get.return_value = {"value": [{"objectId": "abc123"}]}
    for verb in ("put", "post", "patch"):
        getattr(ado.session, verb).return_value = MagicMock(ok=True, status_code=200)
    return ado


def _archived(ado: MagicMock) -> bool:
    """Report whether the one call that disables an ADO repository was made."""
    return any(
        call.kwargs.get("json") == {"isDisabled": True}
        for call in ado.session.patch.call_args_list
    )


# --------------------------------------------------------------------------
# Guard 1 — opt-in
# --------------------------------------------------------------------------


def test_archive_is_off_by_default():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos([REPO])
    assert not _archived(ado), "a live cleanup archived the repo without being asked"
    assert "archive" not in results[0]["actions"]


def test_archive_stays_off_when_the_other_actions_are_requested():
    ado = _ado()
    ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=True,
    )
    assert not _archived(ado)


def test_archive_runs_only_when_explicitly_requested():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=False, archive_repo=True,
    )
    assert _archived(ado)
    assert results[0]["actions"]["archive"] == {"status": "archived"}


def test_archive_is_refused_for_every_repo_that_was_not_named():
    """The switch is per call, not per repo: it applies to the whole batch."""
    ado = _ado()
    others = [
        RepoConfig(ado_project="Contoso", ado_repo=name, gh_org="o", gh_repo=name)
        for name in ("alpha", "beta", "gamma")
    ]
    ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(others, archive_repo=False)
    assert not _archived(ado)


# --------------------------------------------------------------------------
# Guard 2 — mode-gated (CA-001)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, ExecutionMode.DRY_RUN])
def test_dry_run_never_disables_the_repository(mode):
    ado = _ado()
    cleanup = ADOCleanup(ado) if mode is None else ADOCleanup(ado, mode=mode)
    results = cleanup.cleanup_repos([REPO], archive_repo=True)
    assert not _archived(ado)
    assert not ado.get_repo.called, "dry run read the repo it was not going to touch"
    assert results[0]["actions"]["archive"] == {"dry_run": True}


def test_the_rehearsal_marker_is_not_mistaken_for_a_completed_archive():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.DRY_RUN).cleanup_repos(
        [REPO], archive_repo=True,
    )
    archive = results[0]["actions"]["archive"]
    assert archive.get("status") is None, (
        "a dry-run archive reported a status a caller could read as success"
    )


# --------------------------------------------------------------------------
# A refused or failing archive leaves the repository usable
# --------------------------------------------------------------------------


def test_a_refused_archive_reports_the_status_code_and_does_not_raise():
    ado = _ado()
    ado.session.patch.return_value = MagicMock(ok=False, status_code=403)
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], archive_repo=True,
    )
    assert results[0]["actions"]["archive"] == {"status": "failed", "http_code": 403}
    assert results[0]["status"] == "completed"


def test_an_archive_that_cannot_resolve_the_repo_is_never_attempted():
    ado = _ado()
    ado.get_repo.side_effect = RuntimeError("repo lookup failed")
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], archive_repo=True,
    )
    assert not ado.session.patch.called
    assert results[0]["actions"]["archive"]["status"] == "error"
    assert "repo lookup failed" in results[0]["actions"]["archive"]["error"]


def test_the_archive_call_targets_the_resolved_repository_id():
    ado = _ado()
    ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos([REPO], archive_repo=True)
    url, = ado.session.patch.call_args.args
    assert "/_apis/git/repositories/repo-guid?" in url, (
        "the archive was aimed at something other than the resolved repository id"
    )
    assert ado.session.patch.call_args.kwargs["timeout"] == 30
