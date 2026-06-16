"""Executor — whitelisted Accelerator API calls only."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ado2gh.agents.local.tool_catalog import executor_allowlist, get_tool

EXECUTOR_WHITELIST = executor_allowlist()


class AgentExecutor:
    """Runs whitelisted tools; blocks live rollback without approval."""

    def __init__(self, tool_registry: Optional[Dict[str, Callable]] = None):
        self.tools = tool_registry or {}
        self._allowlist = executor_allowlist()

    def can_invoke(self, tool_name: str) -> bool:
        return tool_name in self._allowlist and get_tool(tool_name) is not None

    def invoke(self, tool_name: str, arguments: dict, approver_ok: bool = False) -> dict[str, Any]:
        if not self.can_invoke(tool_name):
            return {"error": f"Tool not whitelisted: {tool_name}"}
        tool = get_tool(tool_name)
        if tool and tool.requires_approval and not arguments.get("dry_run", True) and not approver_ok:
            return {"error": f"Live {tool_name} requires Approver approval"}
        if tool_name == "ado2gh_rollback" and not arguments.get("dry_run", True) and not approver_ok:
            return {"error": "Live rollback requires Approver approval"}
        fn = self.tools.get(tool_name)
        if not fn:
            return {"error": f"Tool not registered: {tool_name}"}
        return {"result": fn(arguments)}
