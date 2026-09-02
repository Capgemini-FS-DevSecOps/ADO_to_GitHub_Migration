"""LangChain tools for the Executor agent.

- call_accelerator: invoke accelerator migration endpoints (GET/POST)
- github_api: read/write GitHub REST API (GET/POST/PATCH/PUT/DELETE)
- ado_api_query: read-only Azure DevOps API

Write tools are wrapped with guardrails (plan approval, dry-run, deletion checks).
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.tools.orchestrator_tools import (
    AdoApiQueryArgs,
    CallAcceleratorArgs,
    GitHubApiArgs,
)
from ado2gh.agents.migration_agent.tools.shared_tools import append_shared_tools


class GeneratePlanArgs(BaseModel):
    phase: str | None = Field(default=None, description="Optional migration phase (only when operator specified)")
    repository_id: str = Field(default="", description="Specific repo as Project/RepoName (optional)")


def get_executor_tools(
    accel_get: Any = None,
    accel_post: Any = None,
    session_token: str | None = None,
    build_plan: Any = None,
    session_getter: Any = None,
    log_decision: Any = None,
    accel_request: Any = None,
) -> list[StructuredTool]:
    """Build the tool set for the Executor agent.

    - call_accelerator: generic accelerator API (GET/POST) — wrapped with guardrails
    - github_api: GitHub REST read/write via accelerator proxy — wrapped with guardrails
    - ado_api_query: read-only ADO API
    - generate_plan: optional, if build_plan is available
    """
    from ado2gh.agents.migration_agent.guardrails import wrap_tool_with_guardrail

    if accel_request is None and (accel_get or accel_post):
        async def _default_accel_request(
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            method_upper = method.upper()
            if method_upper == "GET":
                if not accel_get:
                    return {"error": "accelerator_unavailable"}
                return await accel_get(path, session_token=session_token)
            if method_upper == "POST":
                if not accel_post:
                    return {"error": "accelerator_unavailable"}
                return await accel_post(path, body or {}, session_token=session_token)
            return {"error": f"accelerator_proxy_does_not_support_{method_upper}"}

        accel_request = _default_accel_request

    async def call_accelerator(method: str = "POST", endpoint: str = "", body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call any accelerator API endpoint. The LLM chooses the endpoint and body based on its knowledge of the accelerator API."""
        body = body or {}
        method_upper = method.upper()
        path = endpoint.lstrip("/")
        try:
            if method_upper == "GET":
                if accel_get:
                    return await accel_get(f"/{path}", session_token=session_token)
                return {"error": "accelerator_unavailable"}
            elif method_upper == "POST":
                if accel_post:
                    return await accel_post(f"/{path}", body, session_token=session_token)
                return {"error": "accelerator_unavailable"}
            else:
                return {"error": f"unsupported_method: {method}"}
        except Exception as e:
            return {"error": str(e)}

    async def ado_api_query(endpoint: str) -> dict[str, Any]:
        """Query the Azure DevOps API (read-only). Pass an endpoint path like 'projects/{project}/repos/{repo}'."""
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            path = endpoint.lstrip("/")
            return await accel_get(f"/v1/ado/{path}", session_token=session_token)
        except Exception as e:
            return {"error": str(e)}

    async def github_api(
        endpoint: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        repository_id: str = "",
    ) -> dict[str, Any]:
        """Call the GitHub REST API (read or write). GET for reads; POST/PATCH/PUT/DELETE for writes."""
        path = endpoint.lstrip("/")
        method_upper = method.upper()
        body = body or {}
        request_fn = accel_request
        if request_fn is None and accel_get and method_upper == "GET":
            async def request_fn(m, p, b=None):
                return await accel_get(f"/{p.lstrip('/')}", session_token=session_token)
        if request_fn is None:
            return {"error": "accelerator_unavailable"}
        try:
            return await request_fn(method_upper, f"/v1/github/{path}", body)
        except Exception as e:
            return {"error": str(e)}

    tools = [
        StructuredTool.from_function(
            coroutine=call_accelerator,
            name="call_accelerator",
            description=(
                "Call any accelerator API endpoint (GET or POST). "
                "Available POST endpoints: /v1/migrate/git-mirror, /v1/migrate/pipeline-convert, "
                "/v1/migrate/secret-provision, /v1/migrate/service-connection, /v1/migrate/boards, "
                "/v1/migrate/test-plans, /v1/migrate/artifacts, /v1/migrate/wiki, "
                "/v1/migrate/branch-policies, /v1/migrate/bicep-transform. "
                "Available GET endpoints: /v1/runs/{run_id}, /v1/settings/profiles/{profile_id}/discovery."
            ),
            args_schema=CallAcceleratorArgs,
        ),
        StructuredTool.from_function(
            coroutine=github_api,
            name="github_api",
            description=(
                "Call the GitHub REST API with read or write access. "
                "Use method GET to read (repos, workflows, secrets metadata, branch protection). "
                "Use POST/PATCH/PUT to create or update resources (workflow files, secrets, environments, branch protection). "
                "Use DELETE only when the plan requires removal. "
                "Pass repository_id (Project/RepoName) on writes for guardrail authorization."
            ),
            args_schema=GitHubApiArgs,
        ),
        StructuredTool.from_function(
            coroutine=ado_api_query,
            name="ado_api_query",
            description="Query the Azure DevOps API (read-only). Pass an endpoint path like 'projects/{project}/repos/{repo}'.",
            args_schema=AdoApiQueryArgs,
        ),
    ]

    # Optional: generate_plan if build_plan is available
    if build_plan:
        async def generate_plan(phase: str | None = None, repository_id: str = "") -> dict[str, Any]:
            """Generate a migration plan. If repository_id is given, builds a single-repo plan; otherwise phase-wide."""
            try:
                session: dict[str, Any] = {}
                if repository_id:
                    return await build_plan(session, session_token, repository_id=repository_id, phase=phase)
                return await build_plan(session, session_token, phase=phase)
            except Exception as e:
                return {"error": str(e)}

        tools.append(
            StructuredTool.from_function(
                coroutine=generate_plan,
                name="generate_plan",
                description="Generate a migration plan. If repository_id is given (Project/RepoName), builds a single-repo plan; otherwise builds a phase-wide plan.",
                args_schema=GeneratePlanArgs,
            )
        )

    read_only = {"ado_api_query", "get_current_profile"}
    wrapped = []
    for tool in tools:
        if tool.name in read_only:
            wrapped.append(tool)
        else:
            original_func = tool.coroutine
            guarded = wrap_tool_with_guardrail(
                original_func,
                agent_role="executor",
                session_getter=session_getter,
                log_decision=log_decision,
            )
            tool.coroutine = guarded
            wrapped.append(tool)
    return append_shared_tools(
        wrapped,
        accel_get=accel_get,
        session_token=session_token,
        session_getter=session_getter,
    )
