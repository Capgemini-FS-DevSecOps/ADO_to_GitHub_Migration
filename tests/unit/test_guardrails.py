"""Unit tests for guardrails — plan authorization, resource validation, deletion, audit."""
import pytest
from unittest.mock import MagicMock

from ado2gh.agents.migration_agent.guardrails import (
    GuardrailDecision,
    GuardrailAction,
    evaluate_guardrail,
    wrap_tool_with_guardrail,
)


# ─── GuardrailDecision dataclass ──────────────────────────────────────

def test_guardrail_decision_allow():
    d = GuardrailDecision(
        action=GuardrailAction.ALLOW,
        agent_role="executor",
        tool_name="call_accelerator",
        operation_type="call_accelerator",
        target_resource="Proj/RepoA",
        reason="Plan authorized",
    )
    assert d.allowed is True
    assert d.blocked is False
    assert d.needs_confirmation is False


def test_guardrail_decision_block():
    d = GuardrailDecision(
        action=GuardrailAction.BLOCK,
        agent_role="executor",
        tool_name="call_accelerator",
        operation_type="call_accelerator",
        target_resource="Proj/RepoA",
        reason="No plan",
    )
    assert d.blocked is True
    assert d.allowed is False


def test_guardrail_decision_require_confirmation():
    d = GuardrailDecision(
        action=GuardrailAction.REQUIRE_CONFIRMATION,
        agent_role="executor",
        tool_name="repo_delete",
        operation_type="repo_delete",
        target_resource="Proj/RepoA",
        reason="Deletion requires confirmation",
    )
    assert d.needs_confirmation is True


def test_guardrail_decision_to_dict():
    d = GuardrailDecision(
        action=GuardrailAction.ALLOW,
        agent_role="planner",
        tool_name="ado_api_query",
        operation_type="ado_api_query",
        target_resource="Proj/RepoA",
        reason="Read-only",
    )
    d_dict = d.to_dict()
    assert d_dict["action"] == "allow"
    assert d_dict["agent_role"] == "planner"
    assert d_dict["tool_name"] == "ado_api_query"
    assert "timestamp" in d_dict


# ─── evaluate_guardrail ───────────────────────────────────────────────

def test_read_only_operation_allowed():
    decision = evaluate_guardrail("planner", "get_current_profile", {})
    assert decision.allowed


def test_write_operation_no_plan_blocked():
    # The session is live (not a preview run), so the block here is not the
    # preview-run safeguard — it is the missing plan, checked next (THR-06-005).
    decision = evaluate_guardrail(
        "executor", "call_accelerator", {"method": "POST", "target_resource": "Proj/RepoA"},
        session={"dry_run": False},
    )
    assert decision.blocked
    assert "plan" in decision.reason.lower()


def test_write_operation_plan_not_approved_blocked():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"method": "POST", "target_resource": "Proj/RepoA"},
        migration_plan=plan,
        plan_approved=False,
        session={"dry_run": False},
    )
    assert decision.blocked
    assert "approved" in decision.reason.lower()


def test_write_operation_plan_approved_allowed():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"method": "POST", "target_resource": "Proj/RepoA"},
        migration_plan=plan,
        plan_approved=True,
        session={"dry_run": False},
    )
    assert decision.allowed


def test_github_api_get_allowed_without_plan():
    decision = evaluate_guardrail("executor", "github_api", {"method": "GET", "endpoint": "repos/o/r"})
    assert decision.allowed


def test_github_api_post_blocked_in_dry_run():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "github_api",
        {"method": "POST", "endpoint": "repos/o/r/contents/f", "repository_id": "Proj/RepoA"},
        migration_plan=plan,
        plan_approved=True,
        session={"dry_run": True},
    )
    assert decision.blocked
    assert "dry-run" in decision.reason.lower()


def test_github_api_post_allowed_live_with_plan():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "github_api",
        {"method": "POST", "endpoint": "repos/o/r/contents/f", "repository_id": "Proj/RepoA"},
        migration_plan=plan,
        plan_approved=True,
        session={"dry_run": False},
    )
    assert decision.allowed


def test_write_operation_resource_not_in_plan_blocked():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"method": "POST", "target_resource": "Proj/RepoB"},
        migration_plan=plan,
        plan_approved=True,
        session={"dry_run": False},
    )
    assert decision.blocked
    assert "not in approved plan" in decision.reason.lower()


def test_deletion_operation_requires_confirmation():
    decision = evaluate_guardrail("executor", "repo_delete", {"target_resource": "Proj/RepoA"})
    assert decision.needs_confirmation


def test_unknown_operation_is_not_allowed_by_default():
    decision = evaluate_guardrail("executor", "unknown_tool", {})
    assert decision.blocked


# ─── wrap_tool_with_guardrail ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_wrap_tool_blocks_no_plan():
    async def call_accelerator(**kwargs):
        return {"status": "success"}

    session_getter = lambda: {"migration_plan": None, "plan_approved": False}
    wrapped = wrap_tool_with_guardrail(
        call_accelerator, agent_role="executor", session_getter=session_getter,
    )
    result = await wrapped(
        _tool_name="call_accelerator", method="POST", target_resource="Proj/RepoA",
    )
    assert result["error"] == "guardrail_blocked"


@pytest.mark.asyncio
async def test_wrap_tool_allows_read_only():
    async def ado_api_query(**kwargs):
        return {"data": "ok"}

    session_getter = lambda: {}
    wrapped = wrap_tool_with_guardrail(
        ado_api_query, agent_role="planner", session_getter=session_getter,
    )
    result = await wrapped(_tool_name="get_current_profile")
    assert "error" not in result
    assert result["data"] == "ok"


@pytest.mark.asyncio
async def test_wrap_tool_deletion_requires_confirmation():
    async def repo_delete(**kwargs):
        return {"status": "deleted"}

    session_getter = lambda: {"migration_plan": {"plan_id": "p1", "repos": [{"id": "A"}]}, "plan_approved": True}
    wrapped = wrap_tool_with_guardrail(
        repo_delete, agent_role="executor", session_getter=session_getter,
    )
    result = await wrapped(_tool_name="repo_delete", target_resource="A")
    assert result["error"] == "confirmation_required"


@pytest.mark.asyncio
async def test_wrap_tool_logs_decision():
    async def call_accelerator(**kwargs):
        return {"status": "success"}

    logged = []
    def log_decision(sid, decision):
        logged.append((sid, decision))

    # The approval is recorded against the plan's revision: a bare `plan_approved`
    # with no revision on record is treated as a stale approval and is revoked
    # (THR-09-002).
    from ado2gh.agents.migration_agent.hitl.blockers import plan_revision_key
    from ado2gh.agents.migration_agent.hitl.intake import PLAN_APPROVAL_KEY

    logged_plan = {"plan_id": "p1", "repos": [{"id": "Proj/RepoA"}]}
    session_getter = lambda: {
        "session_id": "ses1",
        "migration_plan": logged_plan,
        "plan_approved": True,
        PLAN_APPROVAL_KEY: plan_revision_key(logged_plan),
        "dry_run": False,
    }
    wrapped = wrap_tool_with_guardrail(
        call_accelerator, agent_role="executor",
        session_getter=session_getter, log_decision=log_decision,
    )
    result = await wrapped(
        _tool_name="call_accelerator", method="POST", target_resource="Proj/RepoA",
    )
    assert result["status"] == "success"
    assert len(logged) == 1
    assert logged[0][0] == "ses1"
    assert logged[0][1]["action"] == "allow"


# ─── The approved-plan scope check must fail closed (THR-06-001) ───────

_LIVE_SESSION = {"dry_run": False}


def test_write_blocked_when_plan_declares_no_repo_scope():
    """An approved plan with no `repos` key authorises nothing (THR-06-001)."""
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"method": "POST", "endpoint": "/v1/migrate/secret-provision"},
        migration_plan={"plan_id": "p1"}, plan_approved=True, session=_LIVE_SESSION,
    )
    assert decision.blocked
    assert "scope" in decision.reason


def test_write_blocked_when_plan_scope_key_drifts():
    """A plan that spells the key `repositories` must not disarm the check."""
    decision = evaluate_guardrail(
        "executor", "github_api",
        {"method": "PUT", "repository_id": "Evil/NotInPlan"},
        migration_plan={"plan_id": "p1", "repositories": [{"id": "Proj/A"}]},
        plan_approved=True, session=_LIVE_SESSION,
    )
    assert decision.blocked


@pytest.mark.parametrize("repos", [[], [{}], [{"id": ""}], [{"id": "Proj/A"}, {}], "Proj/A", None])
def test_write_blocked_when_plan_scope_is_unusable(repos):
    """Empty, malformed or non-list repo scopes all fail closed."""
    decision = evaluate_guardrail(
        "executor", "github_api",
        {"method": "POST", "repository_id": "Proj/A"},
        migration_plan={"plan_id": "p1", "repos": repos},
        plan_approved=True, session=_LIVE_SESSION,
    )
    assert decision.blocked


def test_write_allowed_for_a_repo_the_plan_names():
    """Regression guard: the well-formed path still authorises its own repos."""
    decision = evaluate_guardrail(
        "executor", "github_api",
        {"method": "POST", "repository_id": "Proj/A"},
        migration_plan={"plan_id": "p1", "repos": [{"id": "Proj/A"}]},
        plan_approved=True, session=_LIVE_SESSION,
    )
    assert decision.allowed


# ─── Unregistered tools must fail closed (THR-06-002) ──────────────────

def test_unknown_operation_blocked_by_default():
    """An allowlist whose fall-through is "permit" guards nothing (THR-06-002)."""
    decision = evaluate_guardrail("executor", "provision_secret", {}, session=_LIVE_SESSION)
    assert decision.blocked


def test_every_bound_tool_has_a_guardrail_classification():
    """Every tool the agents bind must be classified, or the terminal BLOCK hits it.

    Read-only use of `tools/*.py`: registering a new tool without adding it to one
    of the three sets fails here rather than at runtime (THR-06-002).
    """
    from ado2gh.agents.migration_agent.guardrails import (
        _DELETION_OPERATIONS,
        _READ_OPERATIONS,
        _WRITE_OPERATIONS,
    )
    from ado2gh.agents.migration_agent.tools.executor_tools import get_executor_tools
    from ado2gh.agents.migration_agent.tools.orchestrator_tools import get_orchestrator_tools
    from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools
    from ado2gh.agents.migration_agent.tools.validator_tools import get_validator_tools

    classified = _READ_OPERATIONS | _WRITE_OPERATIONS | _DELETION_OPERATIONS
    bound = {
        tool.name
        for builder in (
            get_orchestrator_tools, get_planner_tools, get_executor_tools, get_validator_tools,
        )
        for tool in builder()
    }
    assert bound <= classified, f"unclassified tools: {sorted(bound - classified)}"


# ─── An absent session is not live authority (THR-06-005) ──────────────

@pytest.mark.parametrize("session", [None, {}, {"dry_run": None}, {"dry_run": "false"}])
@pytest.mark.parametrize("tool_name", ["github_api", "call_accelerator"])
def test_write_blocked_when_session_carries_no_live_decision(session, tool_name):
    """Only an explicit `dry_run is False` is live authority (CA-001, THR-06-005)."""
    decision = evaluate_guardrail(
        "executor", tool_name,
        {"method": "POST", "repository_id": "Proj/A", "target_resource": "Proj/A"},
        migration_plan={"plan_id": "p1", "repos": [{"id": "Proj/A"}]},
        plan_approved=True, session=session,
    )
    assert decision.blocked
    assert "dry-run" in decision.reason


@pytest.mark.asyncio
async def test_wrap_tool_blocks_write_when_session_getter_returns_none():
    """`wrap_tool_with_guardrail` must survive a session getter that returns None."""
    async def github_api(**kwargs):
        return {"status": "written"}

    wrapped = wrap_tool_with_guardrail(
        github_api, agent_role="executor", session_getter=lambda: None,
    )
    result = await wrapped(_tool_name="github_api", method="POST", repository_id="Proj/A")
    assert result["error"] == "guardrail_blocked"


# ─── No alias may set plan approval (GAP-070) ──────────────────────────

def test_plan_approval_cannot_be_set_through_an_alias():
    """`plan_approved` is the only way to say "the operator approved this" (GAP-070).

    The deleted `approved_plan` alias implied approval from the mere presence of a
    plan, so a caller that only had a plan object handed one in and got a write
    authorised without an operator ever approving it.
    """
    plan = {"plan_id": "p1", "repos": [{"id": "Proj/A"}]}
    with pytest.raises(TypeError):
        evaluate_guardrail(
            "executor", "github_api",
            {"method": "POST", "repository_id": "Proj/A"},
            approved_plan=plan,
            session=_LIVE_SESSION,
        )

    decision = evaluate_guardrail(
        "executor", "github_api",
        {"method": "POST", "repository_id": "Proj/A"},
        migration_plan=plan, session=_LIVE_SESSION,
    )
    assert decision.blocked
    assert "approved" in decision.reason.lower()


def test_plan_scope_refuses_an_entry_it_cannot_identify():
    """`repos: [None]` must not authorise the literal target "None" (Codex review)."""
    from ado2gh.agents.migration_agent.guardrails import plan_scope

    assert plan_scope({"repos": [None]}) is None
    assert plan_scope({"repos": [{"id": "Proj/A"}, 7]}) is None
    assert plan_scope({"repos": ["Proj/A", " Proj/B "]}) == {"Proj/A", "Proj/B"}


# ─── The guardrail's method default must match the tools (GAP-086 follow-up) ───

@pytest.mark.asyncio
async def test_accelerator_call_without_a_method_is_read_through_the_wrapper():
    """An omitted `method` is a GET everywhere else, so the guardrail must agree.

    `CallAcceleratorArgs.method` and `executor_tools.call_accelerator` both default to
    GET; a guardrail that still read the omission as POST would refuse a read for
    want of live authority.
    """
    ran = []

    async def call_accelerator(**kwargs):
        ran.append(kwargs)
        return {"status": "ok"}

    wrapped = wrap_tool_with_guardrail(
        call_accelerator, agent_role="executor", session_getter=lambda: {"dry_run": True},
    )
    result = await wrapped(_tool_name="call_accelerator", endpoint="/v1/settings/profiles/p/discovery")

    assert "error" not in result
    assert len(ran) == 1


def test_accelerator_write_still_needs_an_explicit_method():
    """The GET default must not weaken the write path: POST is still a write."""
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"method": "POST", "endpoint": "/v1/migrate/git-mirror"},
        session={"dry_run": True},
    )
    assert decision.blocked
    assert "dry-run" in decision.reason


# ─── Approval must be bound to the plan revision (THR-09-002 follow-up) ────

def _approved_session(plan):
    """Session with an operator approval recorded against `plan`'s revision."""
    from ado2gh.agents.migration_agent.hitl.blockers import plan_revision_key
    from ado2gh.agents.migration_agent.hitl.intake import PLAN_APPROVAL_KEY

    return {
        "migration_plan": plan,
        "dry_run": False,
        "plan_approved": True,
        PLAN_APPROVAL_KEY: plan_revision_key(plan),
    }


@pytest.mark.asyncio
async def test_wrapped_tool_rejects_an_approval_made_for_an_older_plan_revision():
    """The wrapper must revoke a stale approval itself, not trust an upstream node."""
    async def github_api(**kwargs):
        return {"status": "written"}

    plan = {"plan_id": "p1", "revision": 1, "dry_run": False, "repos": [{"id": "Proj/A"}]}
    session = _approved_session(plan)

    # The planner revises the plan after the operator approved it.
    plan["revision"] = 2
    plan["repos"] = [{"id": "Proj/A"}, {"id": "Proj/B"}]

    wrapped = wrap_tool_with_guardrail(
        github_api, agent_role="executor", session_getter=lambda: session,
    )
    result = await wrapped(_tool_name="github_api", method="POST", repository_id="Proj/A")

    assert result["error"] == "guardrail_blocked"
    assert "approved" in result["message"].lower()
    assert session["plan_approved"] is False


@pytest.mark.asyncio
async def test_wrapped_tool_still_honours_an_approval_of_the_current_revision():
    """No-regression half: an unrevised plan keeps its approval."""
    async def github_api(**kwargs):
        return {"status": "written"}

    plan = {"plan_id": "p1", "revision": 1, "dry_run": False, "repos": [{"id": "Proj/A"}]}
    session = _approved_session(plan)

    wrapped = wrap_tool_with_guardrail(
        github_api, agent_role="executor", session_getter=lambda: session,
    )
    result = await wrapped(_tool_name="github_api", method="POST", repository_id="Proj/A")

    assert result["status"] == "written"
    assert session["plan_approved"] is True
