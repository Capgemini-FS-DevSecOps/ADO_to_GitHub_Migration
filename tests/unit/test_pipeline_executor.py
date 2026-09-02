"""Unit tests for agent pipeline-only execution."""
from ado2gh.agents.migration_agent.nodes.executor.pipeline import (
    agent_live_approved,
    build_executor_result_from_pipeline,
    resolve_agent_repository_id,
)


def test_resolve_agent_repository_id_from_discovery():
    session = {
        "plan_repository_id": "azure-pipelines-build-migration",
        "discovery_snapshot": {
            "repos": [
                {
                    "project": "azure-pipelines",
                    "repo_name": "azure-pipelines-build-migration",
                }
            ]
        },
    }
    assert resolve_agent_repository_id(session, {}) == (
        "azure-pipelines/azure-pipelines-build-migration"
    )


def test_agent_live_approved_when_session_approved():
    session = {"live_approval_status": "approved", "dry_run": False}
    assert agent_live_approved(session, dry_run=False) is True
    assert agent_live_approved(session, dry_run=True) is False


def test_build_executor_result_from_completed_pipeline():
    run = {
        "id": "run-1",
        "status": "completed",
        "steps": [
            {"id": "migrate_repos", "status": "completed", "message": "ok"},
            {"id": "convert_pipelines", "status": "completed", "message": "ok"},
        ],
    }
    result = build_executor_result_from_pipeline(
        run,
        "azure-pipelines/azure-pipelines-build-migration",
        dry_run=False,
    )
    scopes = result["per_repo_results"][0]["scopes"]
    assert scopes["repo"]["status"] == "success"
    assert scopes["pipelines"]["status"] == "success"
    assert result["failures"] == []
