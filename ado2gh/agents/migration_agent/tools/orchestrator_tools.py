"""Tools for the Orchestrator agent.

- ado_api_query: read-only Azure DevOps API
- github_api_query: read-only GitHub API
- invoke_planner: route to the Planner agent for discovery + plan building

The orchestrator collects parameters from the user via request_user_input,
then calls invoke_planner to hand off to the plan-execute-validate loop (PEV) chain.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.tools.shared_tools import append_shared_tools
from ado2gh.api.proxy_prefixes import ADO_PROXY_PREFIX, GITHUB_PROXY_PREFIX


class AdoApiQueryArgs(BaseModel):
    """Arguments for a read-only Azure DevOps API query."""

    endpoint: str = Field(
        description=(
            "ADO API endpoint path to query (read-only). "
            "Examples: 'projects/{project}/repos/{repo}', "
            "'projects/{project}/pipelines/{id}', "
            "'projects/{project}/repos/{repo}/tree?path=/'"
        )
    )


class GitHubApiQueryArgs(BaseModel):
    """Arguments for a read-only GitHub API query."""

    endpoint: str = Field(
        description=(
            "GitHub API endpoint path to query (read-only). "
            "Examples: 'repos/{org}/{repo}', "
            "'repos/{org}/{repo}/actions/workflows', "
            "'orgs/{org}/repos'"
        )
    )


class GitHubApiArgs(BaseModel):
    """Arguments for a GitHub REST call that may read or write."""

    method: str = Field(
        default="GET",
        description="HTTP method: GET, POST, PATCH, PUT, or DELETE",
    )
    endpoint: str = Field(
        description=(
            "GitHub REST API path relative to api.github.com. "
            "Examples: 'repos/{org}/{repo}', "
            "'repos/{org}/{repo}/contents/.github/workflows/ci.yml', "
            "'repos/{org}/{repo}/actions/secrets/{name}'"
        )
    )
    body: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON request body for POST, PATCH, and PUT (omit for GET/DELETE)",
    )
    repository_id: str = Field(
        default="",
        description=(
            "Optional migration plan repo id (Project/RepoName) for guardrail authorization. "
            "Recommended on write operations."
        ),
    )


class CallAcceleratorArgs(BaseModel):
    """Arguments for an accelerator API call."""

    method: str = Field(
        default="GET",
        description="HTTP method: GET or POST. Defaults to GET — pass POST explicitly to write.",
    )
    endpoint: str = Field(
        description=(
            "Accelerator API endpoint path. Available endpoints: "
            "GET /v1/settings/profiles/{profile_id}/discovery — load discovery data (repos, pipelines, phase assignments), "
            "POST /v1/settings/profiles/{profile_id}/scan — trigger a discovery scan, "
            "POST /v1/plan — build a migration plan. Body: {profile_id, phase, repository_id, dry_run}, "
            "GET /v1/runs/{run_id} — check migration run status, "
            "POST /v1/migrate/git-mirror {project, repo_name, github_org, github_repo, dry_run}, "
            "POST /v1/migrate/pipeline-convert {project, repo_name, pipeline_id, github_org, github_repo}, "
            "POST /v1/migrate/secret-provision {github_org, github_repo, secret_name, secret_value, dry_run}, "
            "POST /v1/migrate/service-connection {project, connection_name, github_org, github_repo, dry_run}, "
            "POST /v1/migrate/boards {project, github_org, github_repo, work_item_types, dry_run}, "
            "POST /v1/migrate/test-plans {project, github_org, github_repo, dry_run}, "
            "POST /v1/migrate/artifacts {project, feed_name, github_org, package_type, dry_run}, "
            "POST /v1/migrate/wiki {project, wiki_name, github_org, github_repo, dry_run}, "
            "POST /v1/migrate/branch-policies {project, repo_name, github_org, github_repo, dry_run}, "
            "POST /v1/migrate/bicep-transform {project, repo_name, file_path}"
        )
    )
    body: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON body for POST requests (omit for GET)",
    )


class InvokePlannerArgs(BaseModel):
    """Arguments for handing a single repository to the Planner agent."""

    repository_id: str = Field(
        description="The repository to migrate, in Project/RepoName format."
    )
    dry_run: bool = Field(
        default=True,
        description="True for dry-run mode, false for live execution."
    )
    phase: str | None = Field(
        default=None,
        description="Optional phase name for phase-based migration. Omit for direct repo migration."
    )


class InvokeBulkPlannerArgs(BaseModel):
    """Arguments for handing several repositories to the Planner agent at once."""

    repository_ids: list[str] = Field(
        description=(
            "List of repositories to migrate in bulk, each in Project/RepoName format. "
            "Example: ['azure-pipelines/repo-a', 'azure-pipelines/repo-b']"
        )
    )
    dry_run: bool = Field(
        default=True,
        description="True for dry-run mode, false for live execution."
    )
    phase: str | None = Field(
        default=None,
        description="Optional phase name for phase-based migration. Omit for direct repo migration."
    )


def _build_ado_api_query_tool(
    accel_get: Callable[..., Any] | None, session_token: str | None
) -> StructuredTool:
    """Build the read-only Azure DevOps API query tool."""

    async def ado_api_query(endpoint: str) -> dict[str, Any]:
        """Query the Azure DevOps API (read-only). Pass an endpoint path like 'projects/{project}/repos/{repo}'."""
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            path = endpoint.lstrip("/")
            return await accel_get(f"{ADO_PROXY_PREFIX}/{path}", session_token=session_token)
        except Exception as e:
            return {"error": str(e)}

    return StructuredTool.from_function(
        coroutine=ado_api_query,
        name="ado_api_query",
        description="Query the Azure DevOps API (read-only). Pass an endpoint path like 'projects/{project}/repos/{repo}'.",
        args_schema=AdoApiQueryArgs,
    )


def _build_github_api_query_tool(
    accel_get: Callable[..., Any] | None, session_token: str | None
) -> StructuredTool:
    """Build the read-only GitHub API query tool."""

    async def github_api_query(endpoint: str) -> dict[str, Any]:
        """Query the GitHub API (read-only). Pass an endpoint path like 'repos/{org}/{repo}'."""
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            path = endpoint.lstrip("/")
            return await accel_get(f"{GITHUB_PROXY_PREFIX}/{path}", session_token=session_token)
        except Exception as e:
            return {"error": str(e)}

    return StructuredTool.from_function(
        coroutine=github_api_query,
        name="github_api_query",
        description="Query the GitHub API (read-only). Pass an endpoint path like 'repos/{org}/{repo}'.",
        args_schema=GitHubApiQueryArgs,
    )


def _build_invoke_planner_tool(
    _accel_get: Callable[..., Any] | None, _session_token: str | None
) -> StructuredTool:
    """Build the single-repository Planner handoff tool. Takes no accelerator access — the handoff itself carries no API call."""

    def invoke_planner(repository_id: str, *, dry_run: bool = True, phase: str | None = None) -> dict[str, Any]:
        """Invoke the Planner agent to load discovery data, validate the repository, and build a migration plan.

        Call this AFTER you have collected repository_id and execution mode from the user.
        The Planner will:
        1. Load discovery data from the accelerator
        2. Validate that the repository_id exists
        3. Build a structured migration plan
        4. Return the plan or a repo_not_found error with suggestions
        """
        return {
            "status": "invoking_planner",
            "repository_id": repository_id,
            "dry_run": dry_run,
            "phase": phase,
        }

    return StructuredTool.from_function(
        func=invoke_planner,
        name="invoke_planner",
        description=(
            "Invoke the Planner agent to build a migration plan for a single repository. "
            "Call this AFTER collecting repository_id and execution mode from the user. "
            "The Planner loads discovery data, validates the repo, and builds the plan. "
            "If the repo is not found, it returns suggestions for valid repo names."
        ),
        args_schema=InvokePlannerArgs,
    )


def _build_invoke_bulk_planner_tool(
    _accel_get: Callable[..., Any] | None, _session_token: str | None
) -> StructuredTool:
    """Build the bulk Planner handoff tool. Takes no accelerator access — the handoff itself carries no API call."""

    def invoke_bulk_planner(repository_ids: list[str], *, dry_run: bool = True, phase: str | None = None) -> dict[str, Any]:
        """Invoke the Planner agent for bulk migration of multiple repositories.

        Call this when the user wants to migrate multiple repositories at once.
        Each repository_id should be in Project/RepoName format.
        The Planner will build a migration plan with all repositories queued for
        sequential plan-execute-validate loop (PEV) processing.
        """
        return {
            "status": "invoking_bulk_planner",
            "repository_ids": repository_ids,
            "dry_run": dry_run,
            "phase": phase,
        }

    return StructuredTool.from_function(
        func=invoke_bulk_planner,
        name="invoke_bulk_planner",
        description=(
            "Invoke the Planner agent for bulk migration of multiple repositories. "
            "Use this when the user wants to migrate several repos at once. "
            "Each repository_id should be in Project/RepoName format. "
            "The Planner builds a migration plan with all repos queued for sequential PEV processing."
        ),
        args_schema=InvokeBulkPlannerArgs,
    )


# The orchestrator's tool names are dispatched by name a second time, in
# `nodes/orchestrator_tools.py` (a separate file, currently being edited by
# another change and off limits here). That dispatcher recognizes nine tool
# names in total, but only five of them are ever bound to the language model
# as a callable `StructuredTool` from this module:
#
# - get_current_profile: built by `shared_tools.append_shared_tools`, prepended below.
# - ado_api_query, github_api_query, invoke_planner, invoke_bulk_planner: built here.
#
# The remaining four names are dispatcher-only: the orchestrator node emits
# them as tool calls directly (generate_plan, run_migration_pev,
# fetch_migration_status, request_user_input are never in this module's
# schema, so the language model never sees them as bound tools). They are
# listed here with no builder so this registry stays the complete, one-place
# record of every name the dispatcher must recognize; a test
# (`tests/unit/test_orchestrator_tool_registry.py`) checks the two never
# drift apart.
ORCHESTRATOR_TOOL_REGISTRY: dict[str, Callable[[Callable[..., Any] | None, str | None], StructuredTool] | None] = {
    "get_current_profile": None,
    "ado_api_query": _build_ado_api_query_tool,
    "github_api_query": _build_github_api_query_tool,
    "invoke_planner": _build_invoke_planner_tool,
    "invoke_bulk_planner": _build_invoke_bulk_planner_tool,
    "generate_plan": None,
    "run_migration_pev": None,
    "fetch_migration_status": None,
    "request_user_input": None,
}


def get_orchestrator_tools(
    accel_get: Callable[..., Any] | None = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> list[StructuredTool]:
    """Build the tool set for the Orchestrator agent from ``ORCHESTRATOR_TOOL_REGISTRY``.

    - ado_api_query: generic read-only ADO API access
    - github_api_query: generic read-only GitHub API access
    - invoke_planner / invoke_bulk_planner: route to the Planner agent

    Returns:
        The orchestrator's StructuredTool list with the shared tools prepended.
    """
    tools = [
        builder(accel_get, session_token)
        for builder in ORCHESTRATOR_TOOL_REGISTRY.values()
        if builder is not None
    ]
    return append_shared_tools(
        tools,
        accel_get=accel_get,
        session_token=session_token,
        session_getter=session_getter,
    )
