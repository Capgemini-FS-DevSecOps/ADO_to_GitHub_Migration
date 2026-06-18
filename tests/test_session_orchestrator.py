"""Unit tests for tool-driven PEV session orchestrator guardrails."""
from __future__ import annotations

import json
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
async def test_auto_resolved_phase_chains_build_plan_with_user_reply(base_session):
    base_session["discovery_snapshot"] = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
    }
    base_session["dry_run"] = True
    build_plan = AsyncMock(return_value={
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "work_items": [],
        "pipeline_steps": ["connect", "migrate_repos", "validate"],
        "repos": ["P/r1"],
    })

    class PhaseRequestLLM(StubLLMProvider):
        def complete(self, prompt: str, system: str | None = None) -> str:
            return json.dumps({
                "tool_calls": [{
                    "name": "request_user_input",
                    "arguments": {
                        "reason": "missing_phase",
                        "prompt": "Please specify the migration phase",
                    },
                }],
            })

    result = await process_user_message(
        base_session,
        "rebuild the migration plan in dry-run mode",
        llm=PhaseRequestLLM(),
        llm_degraded=False,
        accel_get=AsyncMock(return_value={"repos": [], "repos_scanned": 0}),
        build_plan=build_plan,
        session_token=None,
        max_iterations=2,
    )

    assert build_plan.called
    assert base_session.get("migration_plan")
    assert result.reply
    assert "poc" in result.reply.lower()
    assert result.pending_form is not None


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
            "repo": "Proj/app",
            "scope": "repo",
            "label": "Proj/app — Migrate repository (git / GEI)",
            "category": "migrate_repo",
            "category_label": "Repository migration",
            "status": "ready",
            "blocker": "",
            "description": "Transfer branches",
        },
        {
            "id": "Proj__app:secrets",
            "repo": "Proj/app",
            "scope": "secrets",
            "label": "Proj/app — Map secrets",
            "category": "manual_setup",
            "category_label": "Manual setup required",
            "status": "blocked",
            "blocker": "Service connection 'azure-sub'",
            "description": "Create secrets",
        },
        {
            "id": "Proj__other:repo",
            "repo": "Proj/other",
            "scope": "repo",
            "label": "Proj/other — Migrate repository (git / GEI)",
            "category": "migrate_repo",
            "category_label": "Repository migration",
            "status": "ready",
            "blocker": "",
            "description": "Transfer branches",
        },
        {
            "id": "Proj__app:wiki",
            "label": "Proj/app — wiki",
            "scope": "wiki",
            "status": "skipped",
        },
    ]
    sync_work_items_to_tasks(base_session, work_items)
    ids = [t["id"] for t in base_session["tasks"]]
    assert "discovery" in ids
    assert "plan" in ids
    assert "Proj__app:repo" not in ids
    assert "scope:repo:migrate_repo:pending" in ids
    repo_task = next(t for t in base_session["tasks"] if t["id"] == "scope:repo:migrate_repo:pending")
    assert repo_task["count"] == 2
    assert "2 repos" in repo_task["label"]
    assert "Proj/app" not in repo_task["label"]
    blocked = next(t for t in base_session["tasks"] if t["id"] == "scope:secrets:manual_setup:blocked")
    assert blocked["status"] == "blocked"
    assert blocked["count"] == 1
    assert blocked["blocker"] == "Service connection 'azure-sub'"


@pytest.mark.asyncio
async def test_retry_after_repo_deleted_chains_scan_discovery_and_plan(base_session):
    """Retry clears stale plan and auto-chains build after scan + discovery."""
    base_session["migration_plan"] = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "repos": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
    }
    base_session["plan_approved"] = True
    base_session["discovery_snapshot"] = {
        "repos_scanned": 1,
        "projects_scanned": 1,
        "pipeline_inventory_count": 1,
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
    }

    scan_result = {
        "repos_scanned": 34,
        "projects_scanned": 3,
        "scanned_at": "2026-06-16T00:00:00Z",
        "org_inventory": {"pipeline_inventory_count": 17, "total_service_connections": 5},
        "status": "ok",
    }
    discovery = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
        "repos_scanned": 1,
        "projects_scanned": 1,
        "pipeline_inventory_count": 17,
    }
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "repos": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
        "narrative": "Ready to retry migration for 1 repo.",
    }

    async def mock_post(path, body, **kwargs):
        assert "scan" in path
        return scan_result

    async def mock_get(path, **kwargs):
        assert "discovery" in path
        return discovery

    async def mock_build(session, token, phase="poc"):
        return plan_doc

    mock_build_fn = AsyncMock(side_effect=mock_build)

    class ScanDiscoveryLLM(StubLLMProvider):
        def complete(self, prompt: str, system: str | None = None) -> str:
            return json.dumps({
                "thinking": "Rescanning ADO after target repo deletion.",
                "tool_calls": [
                    {"name": "run_profile_scan", "arguments": {}},
                    {"name": "fetch_profile_discovery", "arguments": {}},
                ],
                "reply": "I'll rescan the ADO organization to update inventory data.",
            })

    result = await process_user_message(
        base_session,
        "Retry the migration — the repo is deleted",
        llm=ScanDiscoveryLLM(),
        llm_degraded=False,
        accel_get=mock_get,
        accel_post=mock_post,
        build_plan=mock_build_fn,
        session_token=None,
        max_iterations=3,
    )

    assert mock_build_fn.called
    assert base_session.get("migration_plan") == plan_doc
    assert base_session.get("plan_approved") is False
    assert base_session.get("migration_retry") is None
    assert result.pending_form is not None
    assert result.pending_form.get("form_id") == "plan_confirmation"
    assert "retry" in (result.reply or "").lower()
    assert not result.start_pev


@pytest.mark.asyncio
async def test_retry_with_stub_llm_chains_full_workflow(base_session):
    base_session["migration_plan"] = {"phase": "poc", "repo_count": 1, "blocked": False}
    base_session["plan_approved"] = True

    scan_result = {
        "repos_scanned": 2,
        "projects_scanned": 1,
        "org_inventory": {"pipeline_inventory_count": 3},
        "status": "ok",
    }
    discovery = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
        "repos_scanned": 1,
        "pipeline_inventory_count": 3,
    }
    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "repos": ["P/r1"],
        "pipeline_steps": ["connect", "migrate", "validate"],
    }

    async def mock_post(path, body, **kwargs):
        return scan_result

    async def mock_get(path, **kwargs):
        return discovery

    async def mock_build(session, token, phase="poc"):
        return plan_doc

    mock_build_fn = AsyncMock(side_effect=mock_build)

    result = await process_user_message(
        base_session,
        "retry migration repo deleted",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=mock_get,
        accel_post=mock_post,
        build_plan=mock_build_fn,
        session_token=None,
        max_iterations=6,
    )

    assert mock_build_fn.called
    assert base_session.get("migration_plan") == plan_doc
    assert result.pending_form is not None


@pytest.mark.asyncio
async def test_remigrate_does_not_auto_execute_with_prior_approval(base_session):
    """Remigrate must replan and show confirmation — not start PEV immediately."""
    base_session["migration_plan"] = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "repos": ["azure-pipelines/azure-pipelines-script-migration"],
        "pipeline_steps": ["connect", "migrate", "validate"],
        "narrative": "Prior plan",
    }
    base_session["plan_approved"] = True
    base_session["discovery_snapshot"] = {
        "repos": [{"assigned_phase": "poc", "project": "azure-pipelines", "repo_name": "azure-pipelines-script-migration"}],
        "repos_scanned": 1,
        "pipeline_inventory_count": 1,
    }

    plan_doc = {
        "phase": "poc",
        "repo_count": 1,
        "blocked": False,
        "repos": ["azure-pipelines/azure-pipelines-script-migration"],
        "pipeline_steps": ["connect", "migrate", "validate"],
        "narrative": "Remigration plan ready.",
    }

    mock_build = AsyncMock(return_value=plan_doc)

    result = await process_user_message(
        base_session,
        "Please remigrate azure-pipelines/azure-pipelines-script-migration",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=AsyncMock(),
        build_plan=mock_build,
        session_token=None,
        max_iterations=3,
    )

    assert not result.start_pev
    assert base_session.get("plan_approved") is False
    assert result.pending_form is not None
    assert result.pending_form.get("form_id") == "plan_confirmation"
    assert mock_build.called


def test_wants_migration_execute_ignores_remigrate():
    from ado2gh.agents.session_orchestrator import _wants_migration_execute

    assert not _wants_migration_execute("Please remigrate the repo")
    assert _wants_migration_execute("execute dry-run migration")


@pytest.mark.asyncio
async def test_general_question_does_not_run_migration_tools(base_session):
    base_session["discovery_snapshot"] = {
        "repos": [{"assigned_phase": "poc", "project": "P", "repo_name": "r1"}],
        "repos_scanned": 1,
    }
    base_session["migration_plan"] = None

    accel_get = AsyncMock()
    build_plan = AsyncMock()

    result = await process_user_message(
        base_session,
        "What time is it?",
        llm=StubLLMProvider(),
        llm_degraded=True,
        accel_get=accel_get,
        build_plan=build_plan,
        session_token=None,
    )

    assert "UTC" in (result.reply or "")
    assert not result.start_pev
    assert result.pending_form is None
    accel_get.assert_not_called()
    build_plan.assert_not_called()


def test_migration_workflow_inactive_for_time_question():
    from ado2gh.agents.session_orchestrator import _migration_workflow_active

    assert not _migration_workflow_active("What time is it?")
    assert _migration_workflow_active("build migration plan for poc")
