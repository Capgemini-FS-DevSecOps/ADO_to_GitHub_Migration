"""Tool catalog for agent executor and MCP server.

Defines the set of tools available to the migration agent, with metadata
for subagent assignment, approval requirements, and HTTP invocation.
"""
from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    """Metadata for an agent tool."""
    name: str
    description: str
    subagent: str  # "planner", "executor", "validator"
    requires_approval: bool = False
    default_args: dict[str, Any] | None = None
    http_method: str = "POST"
    http_path: str = ""


# Core tools available to the migration agent
_TOOLS: list[Tool] = [
    Tool(
        name="ado2gh_discover",
        description="Discover repositories and their dependencies in Azure DevOps",
        subagent="planner",
        requires_approval=False,
        default_args={"dry_run": True},
        http_method="GET",
        http_path="/v1/settings/profiles/{profile_id}/discovery",
    ),
    Tool(
        name="ado2gh_plan_phase",
        description="Build a migration plan for a specific phase (poc, pilot, full)",
        subagent="planner",
        requires_approval=False,
        default_args={"dry_run": True},
        http_method="POST",
        http_path="/v1/sessions/{session_id}/plan",
    ),
    Tool(
        name="ado2gh_enqueue_job",
        description="Enqueue a migration job for execution",
        subagent="executor",
        requires_approval=True,
        default_args={"dry_run": True},
        http_method="POST",
        http_path="/v1/runs",
    ),
    Tool(
        name="ado2gh_rollback",
        description="Rollback resources created in a migration session",
        subagent="executor",
        requires_approval=True,
        default_args={},
        http_method="POST",
        http_path="/v1/sessions/{session_id}/rollback",
    ),
    Tool(
        name="ado2gh_fetch_status",
        description="Fetch the status of a migration job",
        subagent="validator",
        requires_approval=False,
        default_args={},
        http_method="GET",
        http_path="/v1/runs/{run_id}",
    ),
]


def list_tools() -> list[Tool]:
    """Return all available tools."""
    return _TOOLS.copy()


def get_tool(name: str) -> Tool | None:
    """Get a tool by name, or None if not found."""
    for tool in _TOOLS:
        if tool.name == name:
            return tool
    return None


def executor_allowlist() -> set[str]:
    """Return tool names that the executor is allowed to invoke."""
    return {t.name for t in _TOOLS if t.subagent == "executor"}


def default_arguments(tool: Tool, user_args: dict[str, Any]) -> dict[str, Any]:
    """Merge default arguments with user-provided arguments."""
    if tool.default_args is None:
        return user_args.copy()
    result = tool.default_args.copy()
    result.update(user_args)
    return result


def invoke_tool_http(
    tool_name: str,
    arguments: dict[str, Any],
    accelerator_url: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Invoke a tool via HTTP against the accelerator API."""
    tool = get_tool(tool_name)
    if not tool:
        return {"error": "unknown_tool", "message": f"Tool {tool_name} not found"}

    url = f"{accelerator_url}/v1/tools/{tool_name}"
    try:
        response = httpx.post(url, json=arguments, headers=headers or {}, timeout=30)
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        return {
            "error": "accelerator_unreachable",
            "message": f"Cannot reach accelerator at {accelerator_url}",
            "remediation_steps": [
                "Ensure the accelerator container is running: docker compose up accelerator",
                "Check ACCELERATOR_URL environment variable",
            ],
        }
    except httpx.HTTPStatusError as e:
        return {
            "error": "http_error",
            "status_code": e.response.status_code,
            "message": str(e),
        }
    except Exception as e:
        return {
            "error": "invocation_error",
            "message": str(e),
        }
