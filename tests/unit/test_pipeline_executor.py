"""Unit tests for agent pipeline-only execution."""
import asyncio

from ado2gh.agents.migration_agent.nodes.executor.pipeline import (
    build_executor_result_from_pipeline,
    ensure_agent_pipeline_run,
    ensure_pipeline_running,
    resolve_agent_repository_id,
)
from ado2gh.api.contracts import PipelineRunStartRequest


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


def test_agent_never_self_certifies_live_approval_in_run_body():
    """GAP-004: the agent sends no client-side live-approval claim (FR-006a)."""
    sent: list[tuple[str, dict]] = []

    async def accel_post(path, body, *, session_token=None):
        sent.append((path, body))
        return {"run": {"id": "run-1"}}

    session = {"live_approval_status": "approved"}
    asyncio.run(
        ensure_agent_pipeline_run(
            session, {}, accel_post=accel_post, session_token=None, dry_run=False,
        )
    )
    asyncio.run(
        ensure_pipeline_running("run-1", accel_post=accel_post, session_token=None)
    )

    assert [p for p, _ in sent] == ["/v1/pipeline/runs", "/v1/pipeline/runs/run-1/start"]
    for _, body in sent:
        assert "agent_live_approved" not in body

    # PipelineRunStartRequest forbids unknown fields, so anything the agent sends that
    # the model does not declare is a 422 at runtime. Validate the real body here.
    PipelineRunStartRequest(**sent[0][1])


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
