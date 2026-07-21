"""Validator dry-run vs live behavior."""

from ado2gh.agents.migration_agent.nodes import (
    _build_validator_investigation_context,
    _gather_validator_dry_run_evidence,
    _validate_scope,
)
from ado2gh.agents.migration_agent.utils import resolve_dry_run


def test_resolve_dry_run_prefers_executor_result():
    assert resolve_dry_run(
        {"dry_run": True},
        migration_plan={"dry_run": False},
        executor_result={"dry_run": False},
    ) is False


def test_resolve_dry_run_defaults_true():
    assert resolve_dry_run({}) is True


def test_build_validator_context_dry_run_includes_executor_logs():
    executor = {
        "dry_run": True,
        "per_repo_results": [{"repo": "P/R", "scopes": {"pipelines": {"status": "success"}}}],
        "failures": [],
    }
    ctx = _build_validator_investigation_context(
        executor,
        {"dry_run": True, "repos": [{"id": "P/R"}]},
        {"dry_run": True},
        [],
    )
    assert "DRY RUN" in ctx
    assert "per_repo_results" in ctx
    assert "not evidentiary" not in ctx.lower()


def test_build_validator_context_live_excludes_executor_logs():
    executor = {
        "dry_run": False,
        "per_repo_results": [{"repo": "P/R", "scopes": {"pipelines": {"status": "success"}}}],
        "failures": [],
    }
    ctx = _build_validator_investigation_context(
        executor,
        {"dry_run": False},
        {"dry_run": False},
        [],
    )
    assert "LIVE RUN" in ctx
    assert "repos_processed" in ctx
    assert "per_repo_results" not in ctx


def test_gather_dry_run_evidence_from_executor_scopes():
    executor = {
        "dry_run": True,
        "per_repo_results": [{
            "repo": "Proj/Repo",
            "scopes": {
                "pipelines": {
                    "status": "success",
                    "workflow_files": ["ci.yml", "cd.yml"],
                },
            },
        }],
    }
    session = {
        "dry_run": True,
        "discovery_snapshot": {
            "pipelines": [{"repo_name": "Repo", "name": "build"}],
        },
    }
    findings = _gather_validator_dry_run_evidence(executor, None, session)
    assert len(findings) == 1
    assert findings[0]["executor_workflow_count"] == 2
    assert findings[0]["executor_scopes"]["pipelines"]["status"] == "success"


def test_validate_scope_git_passes_in_dry_run():
    result = _validate_scope(
        "git",
        {"status": "simulated"},
        {"dry_run": True},
        dry_run=True,
    )
    assert result["passed"] is True


def test_validate_scope_git_live_deferred_not_from_status_alone():
    """Live mode scope validation from executor status is skipped in validator_node."""
    result = _validate_scope(
        "git",
        {"status": "success"},
        {"dry_run": False},
        dry_run=False,
    )
    assert result["passed"] is True
