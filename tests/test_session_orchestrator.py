"""Unit tests for tool-driven PEV session orchestrator guardrails."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ado2gh.agents.llm_provider import StubLLMProvider, UnavailableLLMProvider
from ado2gh.agents.session_orchestrator import execute_tool, process_user_message, sync_work_items_to_tasks


@pytest.fixture
def base_session() -> dict:
    return {
        "session_id": "ses_test",
        "profile_id": "lightweight",
        "dry_run": True,
        "plan_phase": "poc",
        "messages": [],
        "tasks": [],
    }


@pytest.mark.asyncio
async def test_build_plan_blocked_without_discovery(base_session):
    result = await execute_tool(
        "build_migration_plan",
        {"phase": "poc"},
        session=base_session,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
        llm=StubLLMProvider(),
    )
    assert result["error"] == "discovery_required"


@pytest.mark.asyncio
async def test_run_pev_blocked_without_plan(base_session):
    base_session["discovery_snapshot"] = {"repos": []}
    result = await execute_tool(
        "run_migration_pev",
        {},
        session=base_session,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
        llm=StubLLMProvider(),
    )
    assert result["error"] == "plan_required"


@pytest.mark.asyncio
async def test_stub_orchestrator_fetches_discovery_then_plans(base_session):
    discovery = {
        "repos": [{"assigned_phase": "poc", "project": "P", "name": "r1"}],
        "repos_scanned": 1,
    }
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "pipeline_steps": ["connect", "migrate", "validate"],
    }

    async def mock_get(path, **kwargs):
        assert "discovery" in path
        return discovery

    async def mock_build(session, token, phase="poc"):
        return plan_doc

    result = await process_user_message(
        base_session,
        "Plan migration for poc",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=mock_get,
        build_plan=mock_build,
        session_token=None,
    )

    assert base_session.get("discovery_snapshot") is not None
    assert base_session.get("migration_plan") == plan_doc
    assert result.reply
    assert any(t["id"] == "discovery" and t["status"] == "completed" for t in result.tasks)
    assert any(t["id"] == "plan" and t["status"] == "completed" for t in result.tasks)


@pytest.mark.asyncio
async def test_request_user_input_auto_resolves_with_discovery(base_session):
    base_session["discovery_snapshot"] = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
    }
    result = await execute_tool(
        "request_user_input",
        {"reason": "missing_phase"},
        session=base_session,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
        llm=StubLLMProvider(),
    )
    assert result.get("auto_resolved") is True
    assert base_session.get("plan_phase") == "poc"
    assert base_session.get("pending_form") is None


@pytest.mark.asyncio
async def test_request_user_input_sets_pending_form(base_session):
    result = await process_user_message(
        base_session,
        "migrate",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=AsyncMock(return_value={"repos": [], "repos_scanned": 0}),
        build_plan=AsyncMock(return_value={"phase": "poc", "repo_count": 0, "blocked": False}),
        session_token=None,
        max_iterations=1,
    )
    form_result = await execute_tool(
        "request_user_input",
        {"reason": "missing_phase", "title": "Pick phase", "force_form": True},
        session=base_session,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
        llm=StubLLMProvider(),
    )
    assert form_result.get("form_id")
    assert base_session.get("pending_form")


@pytest.mark.asyncio
async def test_migration_intent_chains_with_stub_llm_fallback(base_session):
    """Migration prompts use LLM orchestration with stub fallback when model returns non-JSON."""
    discovery = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
        "repos_scanned": 1,
    }
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "narrative": "Ready to migrate 1 repo in poc.",
        "pipeline_steps": ["connect", "migrate", "validate"],
    }

    async def mock_get(path, **kwargs):
        return discovery

    async def mock_build(session, token, phase="poc"):
        return plan_doc

    result = await process_user_message(
        base_session,
        "Plan migration for poc",
        llm=StubLLMProvider(),
        llm_degraded=False,
        accel_get=mock_get,
        build_plan=mock_build,
        session_token=None,
    )

    assert base_session.get("discovery_snapshot") is not None
    assert base_session.get("migration_plan", {}).get("repo_count") == 1
    assert result.reply
    assert not result.start_pev


@pytest.mark.asyncio
async def test_migration_intent_execute_in_one_message_requires_plan_approval(base_session):
    discovery = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
        "repos_scanned": 1,
    }
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "narrative": "Plan ok",
        "repos": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
    }

    async def mock_get(path, **kwargs):
        return discovery

    async def mock_build(session, token, phase="poc"):
        return plan_doc

    result = await process_user_message(
        base_session,
        "execute dry-run migration for poc",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=mock_get,
        build_plan=mock_build,
        session_token=None,
    )

    assert result.start_pev is False
    assert result.pending_form is not None
    assert result.pending_form.get("form_id") == "plan_confirmation"
    assert base_session.get("plan_approved") is False
    assert base_session.get("migration_plan") == plan_doc


@pytest.mark.asyncio
async def test_migration_intent_blocked_when_llm_unconfigured(base_session):
    result = await process_user_message(
        base_session,
        "migrate poc repos",
        llm=UnavailableLLMProvider(),
        llm_degraded=True,
        llm_unconfigured=True,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
    )
    assert "No LLM models are configured" in (result.reply or "")
    assert not result.start_pev


@pytest.mark.asyncio
async def test_migration_intent_execute_after_plan_approved(base_session):
    base_session["discovery_snapshot"] = {"repos": [{"assigned_phase": "poc"}]}
    base_session["migration_plan"] = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "pipeline_steps": ["connect", "migrate", "validate"],
    }
    base_session["plan_approved"] = True

    result = await process_user_message(
        base_session,
        "execute dry-run",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=AsyncMock(),
        build_plan=AsyncMock(),
        session_token=None,
    )

    assert result.start_pev is True


def test_sync_work_items_to_tasks_preserves_pev_tasks(base_session):
    base_session["tasks"] = []
    work_items = [
        {
            "id": "Proj__app:repo",
            "label": "Proj/app — Migrate repository (git / GEI)",
            "category": "migrate_repo",
            "category_label": "Repository migration",
            "status": "ready",
            "blocker": "",
            "description": "Transfer branches",
        },
        {
            "id": "Proj__app:secrets",
            "label": "Proj/app — Map secrets",
            "category": "manual_setup",
            "category_label": "Manual setup required",
            "status": "blocked",
            "blocker": "Service connection 'azure-sub'",
            "description": "Create secrets",
        },
        {
            "id": "Proj__app:wiki",
            "label": "Proj/app — wiki",
            "status": "skipped",
        },
    ]
    sync_work_items_to_tasks(base_session, work_items)
    ids = [t["id"] for t in base_session["tasks"]]
    assert "discovery" in ids
    assert "plan" in ids
    assert "Proj__app:repo" in ids
    assert "Proj__app:secrets" in ids
    assert "Proj__app:wiki" not in ids
    blocked = next(t for t in base_session["tasks"] if t["id"] == "Proj__app:secrets")
    assert blocked["status"] == "blocked"
    assert blocked["blocker"] == "Service connection 'azure-sub'"
