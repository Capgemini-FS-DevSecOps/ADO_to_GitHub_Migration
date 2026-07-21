"""Unit tests for the executor node — execution, rollback, clarification."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from ado2gh.agents.migration_agent.nodes import executor_node, _execute_scope


def _make_state(**kwargs):
    session = kwargs.pop("session", {})
    defaults = {
        "session": session,
        "llm": None,
        "llm_unconfigured": True,
        "capabilities": None,
        "accel_get": None,
        "accel_post": None,
        "session_token": None,
        "iteration": 0,
        "migration_plan": None,
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.asyncio
async def test_executor_no_plan_returns_failure():
    state = _make_state()
    result = await executor_node(state)
    assert result.get("executor_result") is not None
    assert result["executor_result"]["failures"][0]["error_code"] == "no_plan"


@pytest.mark.asyncio
async def test_executor_processes_work_items():
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [{"repo": "Proj/RepoA", "scopes": ["git"]}],
        "dry_run": True,
    }
    state = _make_state(migration_plan=plan)
    result = await executor_node(state)
    assert result.get("executor_result") is not None
    assert len(result["executor_result"]["per_repo_results"]) == 1


@pytest.mark.asyncio
async def test_executor_skips_blocked():
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [{"repo": "Proj/RepoA", "scopes": ["git"], "status": "blocked", "blocked_reasons": ["missing_pat"]}],
        "dry_run": True,
    }
    state = _make_state(migration_plan=plan)
    result = await executor_node(state)
    assert len(result["executor_result"]["skipped"]) == 1
    assert result["executor_result"]["skipped"][0]["repo"] == "Proj/RepoA"


@pytest.mark.asyncio
async def test_executor_tracks_rollback_live():
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [{"repo": "Proj/RepoA", "scopes": ["git"]}],
        "dry_run": False,
    }
    async def mock_post(url, json=None, session_token=None):
        return {"status": "success"}
    state = _make_state(migration_plan=plan, accel_post=mock_post)
    result = await executor_node(state)
    # Rollback records should be created for live execution
    assert len(result.get("rollback_records", [])) >= 1


@pytest.mark.asyncio
async def test_executor_no_rollback_dry_run():
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [{"repo": "Proj/RepoA", "scopes": ["git"]}],
        "dry_run": True,
    }
    async def mock_post(url, json=None, session_token=None):
        return {"status": "success"}
    state = _make_state(migration_plan=plan, accel_post=mock_post)
    result = await executor_node(state)
    # No rollback records for dry-run
    assert len(result.get("rollback_records", [])) == 0


@pytest.mark.asyncio
async def test_executor_increments_iteration():
    plan = {
        "repos": [{"id": "A"}],
        "work_items": [{"repo": "A", "scopes": ["git"]}],
        "dry_run": True,
    }
    state = _make_state(migration_plan=plan, iteration=3)
    result = await executor_node(state)
    assert result["iteration"] == 4


@pytest.mark.asyncio
async def test_executor_records_failures():
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [{"repo": "Proj/RepoA", "scopes": ["git"]}],
        "dry_run": True,
    }
    async def mock_post(url, json=None, session_token=None):
        raise Exception("API error")
    state = _make_state(migration_plan=plan, accel_post=mock_post)
    result = await executor_node(state)
    assert len(result["executor_result"]["failures"]) >= 1


@pytest.mark.asyncio
async def test_execute_scope_git_no_accel():
    result = await _execute_scope("git", {"repo": "A"}, {}, None, None, None, True)
    assert result["status"] == "skipped"
    assert result["error"] == "accelerator_unavailable"


@pytest.mark.asyncio
async def test_execute_scope_unknown_scope():
    result = await _execute_scope("unknown_scope", {"repo": "A"}, {}, None, None, None, True)
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_executor_multiple_scopes():
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [
            {"repo": "Proj/RepoA", "scope": "repo", "status": "ready"},
            {"repo": "Proj/RepoA", "scope": "pipelines", "status": "ready"},
            {"repo": "Proj/RepoA", "scope": "secrets", "status": "ready"},
            {"repo": "Proj/RepoA", "scope": "wiki", "status": "skipped"},
        ],
        "dry_run": True,
    }
    state = _make_state(migration_plan=plan)
    result = await executor_node(state)
    repo_result = result["executor_result"]["per_repo_results"][0]
    assert "repo" in repo_result["scopes"]
    assert "pipelines" in repo_result["scopes"]
    assert "secrets" in repo_result["scopes"]
    assert "wiki" not in repo_result["scopes"]
