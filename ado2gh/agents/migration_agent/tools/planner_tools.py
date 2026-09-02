"""LangChain tools for the Planner agent.

Three generic read-only tools:
- ado_api_query: read-only Azure DevOps API
- github_api_query: read-only GitHub API
- call_accelerator: read-only accelerator API access (GET only)

The planner has no write access — it loads discovery data and queries APIs.
Plan building is done by the planner node code directly, not via call_accelerator.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.tools.orchestrator_tools import (
    AdoApiQueryArgs,
    GitHubApiQueryArgs,
)
from ado2gh.agents.migration_agent.tools.shared_tools import append_shared_tools


class PlannerCallAcceleratorArgs(BaseModel):
    endpoint: str = Field(
        description=(
            "Accelerator API endpoint path (GET only). Available endpoints: "
            "GET /v1/settings/profiles/{profile_id}/discovery — load discovery data (repos, pipelines, phase assignments), "
            "GET /v1/runs/{run_id} — check migration run status"
        )
    )


def get_planner_tools(
    accel_get: Any = None,
    accel_post: Any = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> list[StructuredTool]:
    """Build the read-only tool set for the Planner agent.

    - ado_api_query: generic read-only ADO API access
    - github_api_query: generic read-only GitHub API access
    - call_accelerator: read-only accelerator API access (GET only)
    """

    async def ado_api_query(endpoint: str) -> dict[str, Any]:
        """Query the Azure DevOps API (read-only). Pass an endpoint path like 'projects/{project}/repos/{repo}'."""
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            path = endpoint.lstrip("/")
            return await accel_get(f"/v1/ado/{path}", session_token=session_token)
        except Exception as e:
            return {"error": str(e)}

    async def github_api_query(endpoint: str) -> dict[str, Any]:
        """Query the GitHub API (read-only). Pass an endpoint path like 'repos/{org}/{repo}'."""
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            path = endpoint.lstrip("/")
            return await accel_get(f"/v1/github/{path}", session_token=session_token)
        except Exception as e:
            return {"error": str(e)}

    async def call_accelerator(endpoint: str) -> dict[str, Any]:
        """Call an accelerator API endpoint (GET only, read-only)."""
        ep = endpoint.lstrip("/")
        if not ep:
            return {"error": "endpoint_required"}
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            return await accel_get(f"/{ep}", session_token=session_token)
        except Exception as e:
            return {"error": str(e)}

    tools = [
        StructuredTool.from_function(
            coroutine=ado_api_query,
            name="ado_api_query",
            description="Query the Azure DevOps API (read-only). Pass an endpoint path like 'projects/{project}/repos/{repo}'.",
            args_schema=AdoApiQueryArgs,
        ),
        StructuredTool.from_function(
            coroutine=github_api_query,
            name="github_api_query",
            description="Query the GitHub API (read-only). Pass an endpoint path like 'repos/{org}/{repo}'.",
            args_schema=GitHubApiQueryArgs,
        ),
        StructuredTool.from_function(
            coroutine=call_accelerator,
            name="call_accelerator",
            description=(
                "Call an accelerator API endpoint (GET only, read-only). "
                "Use GET /v1/settings/profiles/{profile_id}/discovery to load discovery data. "
                "Use GET /v1/runs/{run_id} to check run status."
            ),
            args_schema=PlannerCallAcceleratorArgs,
        ),
    ]
    return append_shared_tools(
        tools,
        accel_get=accel_get,
        session_token=session_token,
        session_getter=session_getter,
    )
