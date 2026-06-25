"""MCP tools route handler for the agent service."""
from __future__ import annotations

from fastapi import APIRouter

from ado2gh.agents.migration_agent.constants import TOOL_CATALOG_VERSION
from ado2gh.agents.migration_agent.tool_catalog import list_tools

from ado2gh.agents.migration_agent.route_helpers import _resolve_model_id

router = APIRouter()

MCP_TOOLS = [
    {"name": t.name, "endpoint": f"{t.http_method} {t.http_path}"}
    for t in list_tools()
]


@router.get("/v1/mcp/tools")
def mcp_tools():
    return {"tools": MCP_TOOLS, "version": TOOL_CATALOG_VERSION}
