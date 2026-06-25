"""Unit tests for the planner node — plan generation, revisions, clarification."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from ado2gh.agents.migration_agent.nodes import (
    planner_node,
    _build_heuristic_plan,
    _build_migration_plan_from_llm,
    _build_migration_queue_from_plan,
    _run_planner_research_loop,
)
from langchain_core.messages import HumanMessage, SystemMessage


def _make_state(**kwargs):
    session = kwargs.pop("session", {})
    defaults = {
        "session": session,
        "llm": None,
        "llm_unconfigured": True,
        "capabilities": None,
        "accel_get": None,
        "session_token": None,
        "validation_feedback": None,
        "iteration": 0,
        "migration_plan": None,
    }
    defaults.update(kwargs)
    return defaults


# ─── Heuristic plan builder ───────────────────────────────────────────

def test_heuristic_plan_basic():
    repos = [
        {"id": "Proj/RepoA", "name": "RepoA", "project": "Proj", "repo_name": "RepoA"},
        {"id": "Proj/RepoB", "name": "RepoB", "project": "Proj", "repo_name": "RepoB"},
    ]
    plan = _build_heuristic_plan(repos, {"dry_run": True}, revision=0)
    assert plan["repo_count"] == 2
    assert plan["dry_run"] is True
    assert plan["revision"] == 0
    assert plan["pipeline_step_ids"] == [
        "connect", "analyze_deps", "migrate_repos", "convert_pipelines", "validate",
    ]
    assert len(plan["work_items"]) >= 2


def test_heuristic_plan_topological_sort():
    repos = [
        {"id": "Proj/RepoB", "name": "RepoB", "dependencies": ["Proj/RepoA"]},
        {"id": "Proj/RepoA", "name": "RepoA", "dependencies": []},
    ]
    plan = _build_heuristic_plan(repos, {"dry_run": True}, revision=0)
    # RepoA should come first (no deps)
    assert plan["repos"][0]["id"] == "Proj/RepoA"
    assert plan["repos"][1]["id"] == "Proj/RepoB"


def test_heuristic_plan_blocked_repo():
    repos = [{"id": "Proj/RepoA", "name": "RepoA", "project": "Proj", "repo_name": "RepoA", "blocked": True, "blocked_reasons": ["missing_pat"]}]
    plan = _build_heuristic_plan(repos, {"dry_run": True}, revision=0)
    assert len(plan["blocked_items"]) == 1
    assert plan["blocked_items"][0]["blocked_reasons"] == ["missing_pat"]


def test_heuristic_plan_increments_revision():
    repos = [{"id": "Proj/RepoA", "name": "RepoA", "project": "Proj", "repo_name": "RepoA"}]
    plan = _build_heuristic_plan(repos, {"dry_run": False}, revision=3)
    assert plan["revision"] == 3
    assert plan["dry_run"] is False


def test_heuristic_plan_circular_deps():
    repos = [
        {"id": "A", "name": "A", "dependencies": ["B"]},
        {"id": "B", "name": "B", "dependencies": ["A"]},
    ]
    plan = _build_heuristic_plan(repos, {"dry_run": True}, revision=0)
    # Both repos should be present even with circular deps
    assert plan["repo_count"] == 2


def test_blockers_from_baseline_probes():
    from ado2gh.agents.migration_agent.operator_input import (
        blockers_from_baseline_probes,
        operator_input_from_probe_failures,
    )

    blockers = blockers_from_baseline_probes([
        {
            "repo": "Proj/Missing",
            "github_org_missing": True,
            "ado_repo": {"error": "404"},
            "github_target": {"exists": False},
            "github_org": "",
            "github_repo": "Missing",
        },
    ])
    assert len(blockers) == 1
    req = operator_input_from_probe_failures(blockers, {"plan_repository_id": "Proj/Missing"})
    assert req.fields[0].options[0] == "fix_repository_id"


# ─── LLM plan builder ─────────────────────────────────────────────────

def test_llm_plan_extraction():
    parsed = {
        "repos": [{"id": "Proj/RepoA", "name": "RepoA"}],
        "work_items": [{"repo": "Proj/RepoA", "scopes": ["git"]}],
        "dry_run": False,
        "assumptions": ["Assumed PAT access"],
        "blocked_items": [],
    }
    plan = _build_migration_plan_from_llm(parsed, {"dry_run": True}, revision=1)
    assert plan["repo_count"] == 1
    assert plan["dry_run"] is False  # From LLM, not session
    assert plan["revision"] == 1
    assert len(plan["assumptions"]) == 1
    assert plan["pipeline_step_ids"] == [
        "connect", "analyze_deps", "migrate_repos", "convert_pipelines", "validate",
    ]


# ─── Planner node ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_planner_no_discovery_sets_clarification():
    state = _make_state(session={"discovery_snapshot": {}})
    result = await planner_node(state)
    assert result.get("pending_clarification") is not None
    assert result["pending_clarification"]["to_role"] == "orchestrator"


@pytest.mark.asyncio
async def test_planner_generates_plan_no_llm():
    session = {
        "discovery_snapshot": {"repos": [{"id": "Proj/RepoA", "name": "RepoA"}]},
        "dry_run": True,
    }
    state = _make_state(session=session)
    result = await planner_node(state)
    assert result.get("migration_plan") is not None
    assert result["migration_plan"]["repo_count"] == 1
    assert result.get("pending_clarification") is None


@pytest.mark.asyncio
async def test_planner_increments_iteration():
    session = {"discovery_snapshot": {"repos": [{"id": "A", "name": "A"}]}, "dry_run": True}
    state = _make_state(session=session, iteration=2)
    result = await planner_node(state)
    assert result["iteration"] == 3


@pytest.mark.asyncio
async def test_planner_revised_plan_from_feedback():
    session = {
        "discovery_snapshot": {"repos": [{"id": "A", "name": "A"}]},
        "dry_run": True,
        "migration_plan": {"repos": [{"id": "A"}], "revision": 0, "work_items": []},
    }
    state = _make_state(
        session=session,
        validation_feedback={"failures": ["git_parity"]},
    )
    result = await planner_node(state)
    plan = result["migration_plan"]
    assert plan["revision"] == 1  # Incremented from 0


@pytest.mark.asyncio
async def test_planner_with_llm():
    class FakeLLM:
        def __init__(self):
            self._call = 0

        async def astream(self, messages):
            self._call += 1
            if self._call == 1:
                payload = {
                    "thinking": "Checking ADO pipelines and GitHub target.",
                    "tool_calls": [
                        {"name": "ado_api_query", "arguments": {"endpoint": "projects/Proj/pipelines"}},
                        {"name": "github_api_query", "arguments": {"endpoint": "repos/org/repo"}},
                    ],
                }
            else:
                payload = {
                    "thinking": "Research complete.",
                    "research_complete": True,
                    "repos": [{"id": "Proj/RepoA", "name": "RepoA"}],
                    "work_items": [{"repo": "Proj/RepoA", "scopes": ["git", "pipelines"]}],
                    "dry_run": True,
                    "assumptions": [],
                    "blocked_items": [],
                }
            yield MagicMock(content=json.dumps(payload))

        async def ainvoke(self, messages):
            return MagicMock(content='{"repos": [{"id": "Proj/RepoA", "name": "RepoA"}], "research_complete": true}')

        def bind_tools(self, tools):
            return self

    caps = MagicMock(supports_tool_calling=True, supports_streaming=True, supports_thinking=False)
    session = {
        "discovery_snapshot": {"repos": [{"id": "Proj/RepoA", "name": "RepoA", "project": "Proj", "repo_name": "RepoA"}]},
        "dry_run": True,
    }
    state = _make_state(
        session=session,
        llm=FakeLLM(),
        llm_unconfigured=False,
        capabilities=caps,
        accel_get=AsyncMock(return_value={"value": []}),
    )
    result = await planner_node(state)
    assert result.get("migration_plan") is not None
    assert result.get("pending_clarification") is None


@pytest.mark.asyncio
async def test_planner_hands_off_to_orchestrator_when_queue_complete():
    migration_queue = {
        "items": [{"repo_id": "Proj/RepoA", "work_items": []}],
        "current_index": 1,
        "completed": ["Proj/RepoA"],
        "failed": [],
    }
    state = _make_state(
        validation_result={"passed": True, "dry_run": True},
        migration_queue=migration_queue,
        migration_plan={"repos": [{"id": "Proj/RepoA"}]},
    )
    result = await planner_node(state)
    assert result.get("planner_next") == "orchestrator"
    assert result.get("start_execution") is not True


@pytest.mark.asyncio
async def test_planner_research_loop_requires_min_probes():
    calls = {"n": 0}

    class FakeLLM:
        async def astream(self, messages):
            calls["n"] += 1
            if calls["n"] == 1:
                payload = {
                    "repos": [{"id": "A", "name": "A"}],
                    "dry_run": True,
                }
            elif calls["n"] == 2:
                payload = {
                    "thinking": "Probing APIs",
                    "tool_calls": [
                        {"name": "ado_api_query", "arguments": {"endpoint": "projects/P/repos/R"}},
                        {"name": "github_api_query", "arguments": {"endpoint": "repos/o/r"}},
                    ],
                }
            else:
                payload = {
                    "research_complete": True,
                    "repos": [{"id": "A", "name": "A"}],
                    "dry_run": True,
                }
            yield MagicMock(content=json.dumps(payload))

    session: dict = {}
    state = {"session": session, "capabilities": MagicMock(supports_thinking=False)}
    accel = AsyncMock(return_value={"id": "1"})

    parsed = await _run_planner_research_loop(
        state,
        session,
        FakeLLM(),
        [SystemMessage(content="plan"), HumanMessage(content="go")],
        accel_get=accel,
    )
    assert parsed.get("research_complete") is True
    assert calls["n"] >= 3
    assert accel.await_count >= 2
