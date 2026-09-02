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
    decision = evaluate_guardrail("executor", "call_accelerator", {"target_resource": "Proj/RepoA"})
    assert decision.blocked
    assert "plan" in decision.reason.lower()


def test_write_operation_plan_not_approved_blocked():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"target_resource": "Proj/RepoA"},
        migration_plan=plan,
        plan_approved=False,
    )
    assert decision.blocked
    assert "approved" in decision.reason.lower()


def test_write_operation_plan_approved_allowed():
    plan = {"plan_id": "plan1", "repos": [{"id": "Proj/RepoA"}]}
    decision = evaluate_guardrail(
        "executor", "call_accelerator",
        {"target_resource": "Proj/RepoA"},
        migration_plan=plan,
        plan_approved=True,
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
        {"target_resource": "Proj/RepoB"},
        migration_plan=plan,
        plan_approved=True,
    )
    assert decision.blocked
    assert "not in approved plan" in decision.reason.lower()


def test_deletion_operation_requires_confirmation():
    decision = evaluate_guardrail("executor", "repo_delete", {"target_resource": "Proj/RepoA"})
    assert decision.needs_confirmation


def test_unknown_operation_allowed_by_default():
    decision = evaluate_guardrail("executor", "unknown_tool", {})
    assert decision.allowed


# ─── wrap_tool_with_guardrail ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_wrap_tool_blocks_no_plan():
    async def call_accelerator(**kwargs):
        return {"status": "success"}

    session_getter = lambda: {"migration_plan": None, "plan_approved": False}
    wrapped = wrap_tool_with_guardrail(
        call_accelerator, agent_role="executor", session_getter=session_getter,
    )
    result = await wrapped(_tool_name="call_accelerator", target_resource="Proj/RepoA")
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

    session_getter = lambda: {
        "session_id": "ses1",
        "migration_plan": {"plan_id": "p1", "repos": [{"id": "Proj/RepoA"}]},
        "plan_approved": True,
        "dry_run": False,
    }
    wrapped = wrap_tool_with_guardrail(
        call_accelerator, agent_role="executor",
        session_getter=session_getter, log_decision=log_decision,
    )
    result = await wrapped(_tool_name="call_accelerator", target_resource="Proj/RepoA")
    assert result["status"] == "success"
    assert len(logged) == 1
    assert logged[0][0] == "ses1"
    assert logged[0][1]["action"] == "allow"
