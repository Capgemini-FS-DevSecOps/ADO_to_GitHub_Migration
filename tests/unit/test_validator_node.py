"""Unit tests for the validator node — validation checks, feedback, per-scope reporting."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from ado2gh.agents.migration_agent import nodes
from ado2gh.agents.migration_agent.nodes import validator_node, _validate_scope
from ado2gh.agents.migration_agent.nodes import (
    _advance_migration_queue,
    _build_migration_queue_from_plan,
)
from ado2gh.agents.migration_agent.hitl.operator_input import (
    blockers_from_validator_baseline_probes,
    validation_failures_from_baseline_probes,
)


def _make_state(**kwargs):
    session = kwargs.pop("session", {})
    defaults = {
        "session": session,
        "llm": None,
        "llm_unconfigured": True,
        "capabilities": None,
        "accel_get": None,
        "session_token": None,
        "iteration": 0,
        "pev_retry_count": 0,
        "migration_plan": None,
        "executor_result": None,
    }
    defaults.update(kwargs)
    return defaults


# ─── _validate_scope ──────────────────────────────────────────────────

def test_validate_scope_error():
    result = _validate_scope("git", {"error": "mirror failed"}, None, True)
    assert result["passed"] is False
    assert "mirror failed" in result["failure"]


def test_validate_scope_skipped():
    result = _validate_scope("git", {"status": "skipped"}, None, True)
    assert result["passed"] is True


def test_validate_scope_skipped_with_endpoint_detail_passes():
    result = _validate_scope(
        "wiki",
        {
            "status": "skipped",
            "message": "Accelerator endpoint unavailable for scope 'wiki'",
            "detail": "404 Not Found",
        },
        None,
        True,
    )
    assert result["passed"] is True


def test_validate_scope_pending():
    result = _validate_scope("secrets", {"status": "pending"}, None, True)
    assert result["passed"] is True


def test_validate_scope_git_dry_run():
    result = _validate_scope("git", {"status": "success"}, None, True)
    assert result["passed"] is True
    assert result["evidence"]["dry_run"] is True


def test_validate_scope_git_dry_run_status():
    result = _validate_scope("git", {"status": "dry_run"}, None, True)
    assert result["passed"] is True
    assert result["evidence"]["status"] == "dry_run"


def test_validate_scope_git_live():
    result = _validate_scope("git", {"status": "success"}, None, False)
    assert result["passed"] is True


def test_validate_scope_pipelines():
    result = _validate_scope("pipelines", {"status": "success"}, None, True)
    assert result["passed"] is True


def test_validate_scope_pipelines_validation_errors_fail():
    result = _validate_scope(
        "pipelines",
        {
            "status": "dry_run",
            "validation_failed": 2,
            "validation_errors": ["ci.yml: missing on:", "deploy.yml: missing runs-on"],
            "completed": 3,
        },
        None,
        True,
    )
    assert result["passed"] is False
    assert "missing on" in result["failure"]


def test_validate_scope_pipelines_executor_failed_count():
    result = _validate_scope(
        "pipelines",
        {"status": "partial", "failed": 1, "completed": 2, "message": "1 pipeline failed"},
        None,
        False,
    )
    assert result["passed"] is False


def test_validate_scope_unknown():
    result = _validate_scope("wiki", {"status": "success"}, None, True)
    assert result["passed"] is True


# ─── validator_node ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validator_no_executor_result():
    state = _make_state()
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is False
    assert result["validation_feedback"] is not None


@pytest.mark.asyncio
async def test_validator_all_pass():
    executor_result = {
        "per_repo_results": [{"repo": "Proj/RepoA", "scopes": {"git": {"status": "success"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "Proj/RepoA"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is True
    assert result["validation_feedback"] is None


@pytest.mark.asyncio
async def test_validator_failure_sets_feedback():
    executor_result = {
        "per_repo_results": [{"repo": "Proj/RepoA", "scopes": {"git": {"error": "mirror failed"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "Proj/RepoA"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is False
    assert result["validation_feedback"] is not None
    assert result["validation_feedback"]["retry_recommended"] is True


@pytest.mark.asyncio
async def test_validator_increments_retry_count():
    executor_result = {
        "per_repo_results": [{"repo": "A", "scopes": {"git": {"error": "failed"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "A"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan, pev_retry_count=0)
    result = await validator_node(state)
    assert result["pev_retry_count"] == 1


@pytest.mark.asyncio
async def test_validator_max_retries_sets_escalation_feedback():
    executor_result = {
        "per_repo_results": [{"repo": "A", "scopes": {"git": {"error": "failed"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "A"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan, pev_retry_count=3)
    result = await validator_node(state)
    feedback = result["validation_feedback"]
    assert feedback is not None
    assert feedback.get("escalate") is True
    assert feedback.get("retry_recommended") is False


@pytest.mark.asyncio
async def test_validator_increments_iteration():
    executor_result = {
        "per_repo_results": [{"repo": "A", "scopes": {"git": {"status": "success"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "A"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan, iteration=5)
    result = await validator_node(state)
    assert result["iteration"] == 6


@pytest.mark.asyncio
async def test_validator_plan_consistency_ignores_leading_slash():
    executor_result = {
        "per_repo_results": [
            {"repo": "/azure-pipelines/azure-pipelines-script-migration", "scopes": {"repo": {"status": "skipped"}}},
        ],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "azure-pipelines/azure-pipelines-script-migration"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is True


@pytest.mark.asyncio
async def test_validator_plan_consistency_matches_short_repo_name():
    discovery = {
        "repos": [{
            "project": "azure-pipelines",
            "repo_name": "azure-pipelines-script-migration",
            "name": "azure-pipelines-script-migration",
        }],
    }
    executor_result = {
        "per_repo_results": [
            {"repo": "azure-pipelines-script-migration", "scopes": {"repo": {"status": "skipped"}}},
        ],
        "failures": [],
        "dry_run": True,
    }
    plan = {
        "repos": [{"id": "azure-pipelines/azure-pipelines-script-migration"}],
        "repository_id": "azure-pipelines-script-migration",
        "work_items": [{"repo": "azure-pipelines/azure-pipelines-script-migration"}],
    }
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    state["session"]["discovery_snapshot"] = discovery
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is True


@pytest.mark.asyncio
async def test_validator_plan_consistency_check():
    executor_result = {
        "per_repo_results": [{"repo": "Proj/RepoB", "scopes": {"git": {"status": "success"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "Proj/RepoA"}]}  # Plan has RepoA, executor did RepoB
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    # Should detect plan inconsistency
    failures = result["validation_result"]["failures"]
    assert any("not in plan" in str(f.get("specific_failure", "")) for f in failures)


@pytest.mark.asyncio
async def test_validator_executor_failures_included():
    executor_result = {
        "per_repo_results": [],
        "failures": [{"repo": "A", "scope": "git", "error": "timeout", "error_code": "timeout"}],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "A"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is False
    assert any(f.get("error_code") == "timeout" for f in result["validation_result"]["failures"])


@pytest.mark.asyncio
async def test_validator_passes_dry_run_endpoint_skips():
    executor_result = {
        "per_repo_results": [
            {
                "repo": "Proj/RepoA",
                "scopes": {
                    "repo": {"status": "success"},
                    "wiki": {
                        "status": "skipped",
                        "message": "Accelerator endpoint unavailable for scope 'wiki'",
                    },
                    "secrets": {
                        "status": "skipped",
                        "message": "No operator secret mappings — provide GitHub secret names via operator input",
                    },
                },
            }
        ],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "Proj/RepoA"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is True
    assert result["validation_feedback"] is None


@pytest.mark.asyncio
async def test_validator_advances_queue_without_per_repo_results():
    migration_queue = {
        "items": [{"repo_id": "Proj/RepoA", "work_items": []}],
        "current_index": 0,
        "completed": [],
        "failed": [],
    }
    executor_result = {
        "per_repo_results": [],
        "failures": [],
        "skipped": [],
        "dry_run": True,
        "current_repo_id": "Proj/RepoA",
    }
    plan = {"repos": [{"id": "Proj/RepoA"}]}
    state = _make_state(
        executor_result=executor_result,
        migration_plan=plan,
        migration_queue=migration_queue,
    )
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is True
    assert result["migration_queue"]["current_index"] == 1
    assert "Proj/RepoA" in result["migration_queue"]["completed"]


def test_build_migration_queue_groups_work_items_when_plan_repo_id_missing():
    plan = {
        "repos": [{"name": "RepoA"}],
        "work_items": [
            {"repo": "Proj/RepoA", "scope": "repo", "status": "ready"},
            {"repo": "Proj/RepoA", "scope": "pipelines", "status": "skipped"},
        ],
    }
    queue = _build_migration_queue_from_plan(plan)
    assert len(queue["items"]) == 1
    assert queue["items"][0]["repo_id"] == "Proj/RepoA"
    assert len(queue["items"][0]["work_items"]) == 2


def test_advance_migration_queue_increments_index():
    queue = {
        "items": [{"repo_id": "A", "work_items": []}],
        "current_index": 0,
        "completed": [],
        "failed": [],
    }
    assert _advance_migration_queue(queue, repo_id="A") is True
    assert queue["current_index"] == 1
    assert queue["completed"] == ["A"]
    assert _advance_migration_queue(queue) is False


@pytest.mark.asyncio
async def test_validator_per_scope_reporting():
    executor_result = {
        "per_repo_results": [
            {"repo": "A", "scopes": {"git": {"status": "success"}, "pipelines": {"status": "success"}}},
            {"repo": "B", "scopes": {"git": {"status": "success"}}},
        ],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "A"}, {"id": "B"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    result = await validator_node(state)
    per_scope = result["validation_result"]["per_scope"]
    assert "git" in per_scope
    assert "pipelines" in per_scope
    assert len(per_scope["git"]["repos"]) == 2


@pytest.mark.asyncio
async def test_validator_fr036_sets_no_retry_escalation():
    executor_result = {
        "per_repo_results": [
            {
                "repo": "azure-pipelines/build-migration",
                "scopes": {
                    "repo": {
                        "status": "failed",
                        "error": "repo already has active live migration (FR-036)",
                    },
                },
            },
        ],
        "failures": [{
            "repo": "azure-pipelines/build-migration",
            "scope": "repo",
            "error": "repo already has active live migration (FR-036)",
        }],
        "dry_run": False,
    }
    plan = {"repos": [{"id": "azure-pipelines/build-migration"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan, pev_retry_count=0)
    result = await validator_node(state)
    feedback = result.get("validation_feedback") or {}
    assert feedback.get("retry_recommended") is False
    assert feedback.get("escalate") is True
    failures = result["validation_result"]["failures"]
    assert any(f.get("error_code") == "migration_in_progress" for f in failures)


def test_validation_failures_from_baseline_probes_missing_github():
    findings = [{
        "repo": "Proj/RepoA",
        "github_org": "my-org",
        "github_repo": "repo-a",
        "ado_repo": {"id": "ado-1"},
        "github_target": {"exists": False},
    }]
    failures = validation_failures_from_baseline_probes(findings)
    assert failures
    assert failures[0]["operator_input_required"] is True
    assert "does not exist" in failures[0]["specific_failure"]


def test_blockers_from_validator_pipeline_conversion_gap():
    findings = [{
        "repo": "Proj/RepoA",
        "pipeline_conversion_gap": True,
        "ado_pipelines_probe": {"pipeline_count": 2},
        "executor_workflow_count": 0,
    }]
    blockers = blockers_from_validator_baseline_probes(findings)
    assert len(blockers) == 1
    assert blockers[0]["scope"] == "pipelines"
    assert "2 pipeline" in blockers[0]["blocker"]


@pytest.mark.asyncio
async def test_validator_baseline_probe_escalates_operator_input(monkeypatch):
    async def fake_probes(*_args, **_kwargs):
        return [{
            "repo": "Proj/RepoA",
            "github_org": "my-org",
            "github_repo": "repo-a",
            "ado_repo": {"id": "ado-1"},
            "github_target": {"exists": False},
        }]

    monkeypatch.setattr(nodes, "_gather_validator_baseline_probes", fake_probes)
    monkeypatch.setattr(
        nodes,
        "_run_validator_llm_investigation",
        AsyncMock(return_value=None),
    )

    executor_result = {
        "per_repo_results": [{"repo": "Proj/RepoA", "scopes": {"git": {"status": "success"}}}],
        "failures": [],
        "dry_run": True,
    }
    plan = {"repos": [{"id": "Proj/RepoA"}]}
    state = _make_state(executor_result=executor_result, migration_plan=plan)
    state["accel_get"] = AsyncMock()
    result = await validator_node(state)
    assert result["validation_result"]["passed"] is False
    assert result.get("pending_operator_input")
    assert result["validation_feedback"]["escalate"] is True
