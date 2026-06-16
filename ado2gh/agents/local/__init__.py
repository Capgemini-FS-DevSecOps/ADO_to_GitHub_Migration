"""Local IDE agent profiles, tool catalog, audit bridge, and stub LLM."""
from ado2gh.agents.local.profiles import LocalAgentProfile, get_profile, list_profile_ids
from ado2gh.agents.local.tool_catalog import (
    TOOL_CATALOG_VERSION,
    ToolContract,
    executor_allowlist,
    get_tool,
    list_tools,
)

__all__ = [
    "LocalAgentProfile",
    "get_profile",
    "list_profile_ids",
    "TOOL_CATALOG_VERSION",
    "ToolContract",
    "executor_allowlist",
    "get_tool",
    "list_tools",
]
