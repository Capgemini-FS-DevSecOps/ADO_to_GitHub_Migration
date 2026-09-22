"""Regression check for register entry GAP-128 — a GitHub write's approval covers
only the ``repository_id``/``target_resource`` argument, never the ``endpoint`` the
request actually reaches. An approved repository could be paired with an endpoint
naming a different one and the write would still go through.
"""

from ado2gh.agents.migration_agent.guardrails import GuardrailAction, evaluate_guardrail

_LIVE_SESSION = {"dry_run": False}

_PLAN = {
    "plan_id": "plan-gap-128",
    "repos": [
        {"id": "Proj/RepoA", "gh_org": "approved-org", "gh_repo": "approved-repo"},
    ],
}


def test_write_to_endpoint_matching_approved_repo_is_allowed():
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "POST",
            "endpoint": "repos/approved-org/approved-repo/issues",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.ALLOW
    assert decision.target_resource == "approved-org/approved-repo"


def test_write_to_endpoint_naming_a_different_org_is_blocked():
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "POST",
            "endpoint": "repos/other-org/other-repo/issues",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.BLOCK
    assert "approved" in decision.reason.lower()
    assert "approved-org/approved-repo" in decision.reason
    assert "other-org/other-repo" in decision.reason


def test_write_to_a_non_repository_endpoint_is_blocked():
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "POST",
            "endpoint": "orgs/acme/teams",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.BLOCK
    assert "repository path" in decision.reason.lower()


def test_dot_segments_that_collapse_to_a_different_repo_are_blocked():
    """The raw string names the approved repo, but dot-segments resolve it elsewhere.

    ``join_api_path`` collapses ``..`` the same way the transport does before
    the request goes out, so the check has to resolve them too or a string
    that reads as the approved repository here reaches a different one on
    the wire.
    """
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "POST",
            "endpoint": "repos/approved-org/approved-repo/../../other-org/other-repo/issues",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.BLOCK
    assert "other-org/other-repo" in decision.reason


def test_a_query_string_cannot_smuggle_a_repository_path_past_a_non_repo_endpoint():
    """An org-level endpoint stays an org-level endpoint no matter what its query says."""
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "POST",
            "endpoint": "orgs/other-org/teams?x=/repos/approved-org/approved-repo",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.BLOCK
    assert "repository path" in decision.reason.lower()


def test_a_fragment_cannot_hide_the_real_segment_from_the_scan():
    """httpx drops everything from the first ``#``, so a fragment must not either."""
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "POST",
            "endpoint": "orgs/other-org/teams#/../../repos/approved-org/approved-repo",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.BLOCK
    assert "repository path" in decision.reason.lower()


def test_get_to_a_foreign_repo_is_still_allowed_as_a_read():
    decision = evaluate_guardrail(
        "executor",
        "github_api",
        {
            "method": "GET",
            "endpoint": "repos/other-org/other-repo/issues",
            "repository_id": "Proj/RepoA",
        },
        migration_plan=_PLAN,
        plan_approved=True,
        session=_LIVE_SESSION,
    )
    assert decision.action is GuardrailAction.ALLOW
