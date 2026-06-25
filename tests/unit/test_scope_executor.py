"""Unit tests for agent scope execution via accelerator API."""
import pytest

from ado2gh.agents.migration_agent.scope_executor import (
    default_agent_enabled_scopes,
    execute_migration_scope,
    has_secret_dependencies,
    repo_dependency_report,
    resolve_agent_enabled_scopes,
    resolve_repo_context,
)


def test_default_agent_enabled_scopes_core_only():
    scopes = default_agent_enabled_scopes()
    assert scopes == ["repo", "pipelines"]


def test_resolve_agent_enabled_scopes_repo_only_without_pipelines():
    scopes = resolve_agent_enabled_scopes("Proj/RepoA", pipeline_count=0)
    assert scopes == ["repo"]


def test_resolve_agent_enabled_scopes_includes_pipelines_without_secrets_when_no_deps():
    scopes = resolve_agent_enabled_scopes("Proj/RepoA", pipeline_count=2)
    assert scopes == ["repo", "pipelines"]


def test_resolve_agent_enabled_scopes_includes_secrets_when_deps_detected():
    session = {
        "discovery_snapshot": {
            "inventory_gaps": [
                {
                    "type": "service_connection",
                    "project": "Proj",
                    "name": "Azure-Prod",
                }
            ]
        }
    }
    scopes = resolve_agent_enabled_scopes(
        "Proj/RepoA",
        pipeline_count=1,
        session=session,
    )
    assert scopes == ["repo", "pipelines", "secrets"]


def test_resolve_agent_enabled_scopes_adds_metadata_when_detected():
    scopes = resolve_agent_enabled_scopes(
        "Proj/RepoA",
        repo_discovery={"repo_features": {"wiki": {"detected": True}}},
        pipeline_count=0,
    )
    assert scopes == ["repo", "wiki"]


def test_has_secret_dependencies_requires_pipelines_and_inventory_gaps():
    report = repo_dependency_report(
        "Proj/RepoA",
        session={
            "discovery_snapshot": {
                "inventory_gaps": [
                    {"type": "service_connection", "project": "Proj", "name": "sc1"},
                ]
            }
        },
    )
    assert has_secret_dependencies(report, pipeline_count=1) is True
    assert has_secret_dependencies(report, pipeline_count=0) is False
    assert has_secret_dependencies({"service_connections": []}, pipeline_count=2) is False


def test_resolve_repo_context_from_work_item():
    ctx = resolve_repo_context(
        {"repo": "Proj/RepoA", "gh_target": "my-org/RepoA"},
        {"repos": [{"id": "Proj/RepoA"}]},
    )
    assert ctx["project"] == "Proj"
    assert ctx["repo_name"] == "RepoA"
    assert ctx["github_org"] == "my-org"
    assert ctx["github_repo"] == "RepoA"


def test_resolve_repo_context_ignores_leading_slash_gh_target(monkeypatch):
    monkeypatch.setattr(
        "ado2gh.agents.migration_agent.pipeline_plan.resolve_github_org",
        lambda **kwargs: "profile-org",
    )
    ctx = resolve_repo_context(
        {"repo": "Proj/RepoA", "gh_target": "/RepoA"},
        {},
    )
    assert ctx["github_org"] == "profile-org"
    assert ctx["github_repo"] == "RepoA"


def test_resolve_repo_context_fills_project_from_discovery():
    session = {
        "discovery_snapshot": {
            "repos": [
                {
                    "project": "MigrationLab",
                    "repo_name": "azure-pipelines-script-migration",
                    "name": "azure-pipelines-script-migration",
                }
            ]
        }
    }
    ctx = resolve_repo_context(
        {"repo": "azure-pipelines-script-migration"},
        {},
        session=session,
    )
    assert ctx["project"] == "MigrationLab"
    assert ctx["repo_name"] == "azure-pipelines-script-migration"
    assert ctx["repo_id"] == "MigrationLab/azure-pipelines-script-migration"


@pytest.mark.asyncio
async def test_execute_work_items_calls_boards_endpoint():
    calls: list[tuple[str, dict]] = []

    async def mock_post(path, body, session_token=None):
        calls.append((path, body))
        return {"status": "dry_run", "work_item_count": 3}

    result = await execute_migration_scope(
        "work_items",
        {"repo": "Proj/RepoA", "gh_target": "org/RepoA"},
        {},
        accel_post=mock_post,
        session_token="tok",
        dry_run=True,
    )
    assert result["work_item_count"] == 3
    assert calls[0][0] == "/v1/migrate/boards"
    assert calls[0][1]["project"] == "Proj"


@pytest.mark.asyncio
async def test_execute_branch_policies_calls_endpoint():
    calls: list[str] = []

    async def mock_post(path, body, session_token=None):
        calls.append(path)
        return {"status": "dry_run", "policies_found": 2}

    await execute_migration_scope(
        "branch_policies",
        {"repo": "Proj/RepoA", "gh_target": "org/RepoA"},
        {},
        accel_post=mock_post,
        session_token=None,
        dry_run=True,
    )
    assert calls == ["/v1/migrate/branch-policies"]


@pytest.mark.asyncio
async def test_execute_repo_404_includes_endpoint():
    async def mock_post(path, body, session_token=None):
        raise RuntimeError("404 Client Error: Not Found for url http://localhost:8080/v1/migrate/git-mirror")

    result = await execute_migration_scope(
        "repo",
        {"repo": "Proj/RepoA", "gh_target": "org/RepoA"},
        {},
        accel_post=mock_post,
        session_token=None,
        dry_run=True,
    )
    assert result["status"] == "skipped"
    assert result["endpoint"] == "POST /v1/migrate/git-mirror"
    assert "git-mirror" in result["message"]


@pytest.mark.asyncio
async def test_execute_secrets_uses_operator_mappings():
    calls: list[str] = []

    async def mock_post(path, body, session_token=None):
        calls.append(path)
        return {"status": "dry_run"}

    result = await execute_migration_scope(
        "secrets",
        {"repo": "Proj/RepoA", "gh_target": "org/RepoA"},
        {},
        accel_post=mock_post,
        session_token=None,
        dry_run=True,
        secret_mappings={"secret_mapping__Proj__azure-prod": "AZURE_CLIENT_ID"},
    )
    assert result["status"] == "success"
    assert calls[0] == "/v1/migrate/service-connection"
