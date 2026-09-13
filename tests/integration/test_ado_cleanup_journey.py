"""The `ado-cleanup` journey end to end against a faked ADO client (COV-DRIFT-004).

`ado-cleanup` is step 10 of the documented execution workflow and the most
destructive command the CLI ships: it disables the source pipelines, pushes a
`MIGRATION_NOTICE.md` over the default branch and can disable the ADO repository
outright. 103 of its 133 statements had no test exercising them.

Everything here runs against a `MagicMock` standing in for `ADOClient`, so every
recorded call is a request that would have gone to Azure DevOps. The central
assertion in dry-run mode is the *absence* of those calls (CA-001).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ado2gh.core.ado_cleanup import ADOCleanup
from ado2gh.models import ExecutionMode, RepoConfig

REPO = RepoConfig(
    ado_project="Contoso Core",
    ado_repo="payments",
    gh_org="fake-gh-org",
    gh_repo="payments",
)


def _ado(*, ok: bool = True) -> MagicMock:
    """An ADO client double holding one pipeline that belongs to ``REPO``."""
    ado = MagicMock()
    ado.org_url = "https://dev.azure.com/fake-org"
    ado.API = "api-version=7.1"
    ado._encode_project.side_effect = lambda project: project.replace(" ", "%20")
    ado.list_all_pipelines.return_value = [{"id": 11}, {"id": 22}]
    ado.get_build_definition_full.side_effect = lambda _project, pipe_id: {
        "id": pipe_id,
        "name": f"build-{pipe_id}",
        # Only the first pipeline belongs to this repo.
        "repository": {"name": "payments" if pipe_id == 11 else "other-repo"},
    }
    ado.get_repo.return_value = {"id": "repo-guid", "defaultBranch": "refs/heads/main"}
    ado._get.return_value = {"value": [{"objectId": "abc123"}]}
    ado.session.put.return_value = MagicMock(ok=ok, status_code=200 if ok else 409)
    ado.session.post.return_value = MagicMock(ok=ok, status_code=201 if ok else 409)
    ado.session.patch.return_value = MagicMock(ok=ok, status_code=200 if ok else 409)
    return ado


def _mutating_calls(ado: MagicMock) -> list[str]:
    return [
        verb for verb in ("put", "post", "patch", "delete")
        if getattr(ado.session, verb).called
    ]


# --------------------------------------------------------------------------
# Dry run changes nothing (CA-001)
# --------------------------------------------------------------------------


def test_dry_run_makes_no_mutating_call_even_with_every_action_enabled():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.DRY_RUN).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=True, archive_repo=True,
    )
    assert _mutating_calls(ado) == [], (
        f"dry run reached Azure DevOps with {_mutating_calls(ado)}"
    )
    actions = results[0]["actions"]
    assert actions["disable_pipelines"]["dry_run"] is True
    assert actions["redirect"] == {"dry_run": True}
    assert actions["archive"] == {"dry_run": True}


def test_dry_run_still_reports_how_many_pipelines_it_would_disable():
    """The point of the rehearsal is the count, so it is computed even in dry run."""
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.DRY_RUN).cleanup_repos([REPO])
    stats = results[0]["actions"]["disable_pipelines"]
    assert stats["total"] == 1, "the pipeline belonging to another repo was counted"
    assert stats["disabled"] == 0
    assert stats["failed"] == 0


def test_dry_run_is_the_default_mode():
    """Constructing without a mode must not be able to change anything."""
    ado = _ado()
    ADOCleanup(ado).cleanup_repos([REPO], archive_repo=True)
    assert _mutating_calls(ado) == []


# --------------------------------------------------------------------------
# The live journey: disable pipelines -> push notice -> archive
# --------------------------------------------------------------------------


def test_live_run_disables_only_the_pipelines_belonging_to_the_repo():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=False, archive_repo=False,
    )
    assert results[0]["actions"]["disable_pipelines"] == {
        "disabled": 1, "failed": 0, "total": 1,
    }
    assert ado.session.put.call_count == 1
    url = ado.session.put.call_args.args[0]
    assert url == (
        "https://dev.azure.com/fake-org/Contoso%20Core"
        "/_apis/build/definitions/11?api-version=7.1"
    )
    assert ado.session.put.call_args.kwargs["json"]["queueStatus"] == "disabled"


def test_live_run_pushes_the_migration_notice_onto_the_default_branch():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=True, archive_repo=False,
    )
    assert results[0]["actions"]["redirect"] == {"status": "added"}
    push_url, = ado.session.post.call_args.args
    assert push_url == (
        "https://dev.azure.com/fake-org/Contoso%20Core"
        "/_apis/git/repositories/repo-guid/pushes?api-version=7.1"
    )
    body = ado.session.post.call_args.kwargs["json"]
    ref_update, = body["refUpdates"]
    assert ref_update == {"name": "refs/heads/main", "oldObjectId": "abc123"}
    change, = body["commits"][0]["changes"]
    assert change["changeType"] == "add"
    assert change["item"]["path"] == "/MIGRATION_NOTICE.md"
    content = change["newContent"]["content"]
    assert "https://github.com/fake-gh-org/payments" in content
    assert "git remote set-url origin" in content


def test_live_run_archives_the_repo_by_disabling_it():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=False, archive_repo=True,
    )
    assert results[0]["actions"]["archive"] == {"status": "archived"}
    url, = ado.session.patch.call_args.args
    assert url == (
        "https://dev.azure.com/fake-org/Contoso%20Core"
        "/_apis/git/repositories/repo-guid?api-version=7.1"
    )
    assert ado.session.patch.call_args.kwargs["json"] == {"isDisabled": True}


def test_the_full_journey_runs_every_action_and_reports_all_three():
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=True, archive_repo=True,
    )
    result = results[0]
    assert result["status"] == "completed"
    assert result["ado_project"] == "Contoso Core"
    assert result["ado_repo"] == "payments"
    assert result["gh_target"] == "fake-gh-org/payments"
    assert set(result["actions"]) == {"disable_pipelines", "redirect", "archive"}
    assert sorted(_mutating_calls(ado)) == ["patch", "post", "put"]


@pytest.mark.parametrize(
    "disable,redirect,archive,expected",
    [
        (True, False, False, {"disable_pipelines"}),
        (False, True, False, {"redirect"}),
        (False, False, True, {"archive"}),
        (False, False, False, set()),
    ],
)
def test_only_the_requested_actions_are_reported(disable, redirect, archive, expected):
    ado = _ado()
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO],
        disable_pipelines=disable,
        add_redirect=redirect,
        archive_repo=archive,
    )
    assert set(results[0]["actions"]) == expected


# --------------------------------------------------------------------------
# Failure handling: a step that fails is reported, never raised
# --------------------------------------------------------------------------


def test_a_refused_pipeline_disable_is_counted_as_failed():
    ado = _ado(ok=False)
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=False, archive_repo=False,
    )
    assert results[0]["actions"]["disable_pipelines"] == {
        "disabled": 0, "failed": 1, "total": 1,
    }
    assert results[0]["status"] == "completed", "a refusal must not abort the run"


def test_a_refused_notice_push_reports_the_upstream_status_code():
    ado = _ado(ok=False)
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=True, archive_repo=False,
    )
    assert results[0]["actions"]["redirect"] == {"status": "failed", "http_code": 409}


def test_a_refused_archive_reports_the_upstream_status_code():
    ado = _ado(ok=False)
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=False, archive_repo=True,
    )
    assert results[0]["actions"]["archive"] == {"status": "failed", "http_code": 409}


def test_a_repo_with_no_default_branch_ref_skips_the_notice_without_pushing():
    ado = _ado()
    ado._get.return_value = {"value": []}
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=True, archive_repo=False,
    )
    assert results[0]["actions"]["redirect"] == {
        "status": "skipped", "reason": "no default branch ref",
    }
    assert not ado.session.post.called


def test_a_raising_notice_step_is_reported_as_an_error_not_propagated():
    ado = _ado()
    ado.get_repo.side_effect = RuntimeError("ADO unreachable")
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=True, archive_repo=False,
    )
    redirect = results[0]["actions"]["redirect"]
    assert redirect["status"] == "error"
    assert "ADO unreachable" in redirect["error"]


def test_a_raising_archive_step_is_reported_as_an_error_not_propagated():
    ado = _ado()
    ado.get_repo.side_effect = RuntimeError("ADO unreachable")
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=False, add_redirect=False, archive_repo=True,
    )
    assert results[0]["actions"]["archive"]["status"] == "error"


def test_a_pipeline_definition_that_cannot_be_read_is_skipped_not_fatal():
    ado = _ado()
    ado.get_build_definition_full.side_effect = RuntimeError("definition gone")
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=False, archive_repo=False,
    )
    assert results[0]["actions"]["disable_pipelines"]["total"] == 0
    assert not ado.session.put.called


def test_a_repo_whose_cleanup_raises_outright_is_reported_as_an_error_row():
    ado = _ado()
    ado.list_all_pipelines.side_effect = RuntimeError("org unreachable")
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos([REPO])
    assert results[0]["status"] == "error"
    assert "org unreachable" in results[0]["error"]
    assert results[0]["ado_repo"] == "payments"


# --------------------------------------------------------------------------
# Symmetric rollback (FR-026a)
# --------------------------------------------------------------------------


def test_re_enabling_pipelines_reverses_the_disable_with_the_same_url():
    ado = _ado()
    stats = ADOCleanup(ado, mode=ExecutionMode.LIVE).enable_pipelines(REPO)
    assert stats == {"enabled": 1, "failed": 0, "total": 1}
    assert ado.session.put.call_args.kwargs["json"]["queueStatus"] == "enabled"
    assert ado.session.put.call_args.args[0] == (
        "https://dev.azure.com/fake-org/Contoso%20Core"
        "/_apis/build/definitions/11?api-version=7.1"
    )


def test_a_refused_re_enable_is_counted_as_failed():
    ado = _ado(ok=False)
    stats = ADOCleanup(ado, mode=ExecutionMode.LIVE).enable_pipelines(REPO)
    assert stats == {"enabled": 0, "failed": 1, "total": 1}


def test_multiple_repos_each_get_their_own_result_row():
    ado = _ado()
    other = RepoConfig(
        ado_project="Contoso Core", ado_repo="other-repo",
        gh_org="fake-gh-org", gh_repo="other-repo",
    )
    results = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO, other], disable_pipelines=True, add_redirect=False, archive_repo=False,
    )
    assert len(results) == 2
    assert {r["ado_repo"] for r in results} == {"payments", "other-repo"}
    assert all(r["status"] == "completed" for r in results)
