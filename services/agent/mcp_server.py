"""MCP server exposing Accelerator operations as tools."""
from __future__ import annotations

import json
import os
import sys

from ado2gh.agents.local.audit_bridge import IdeAuditBridge
from ado2gh.agents.local.tool_catalog import (
    get_tool,
    invoke_tool_http,
    list_tools,
)

ACCEL_URL = os.environ.get("ACCELERATOR_URL", "http://localhost:8080")
PROFILE_ID = os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight")


def _headers() -> dict:
    cookie = os.environ.get("ADO2GH_SESSION_COOKIE", "")
    if cookie:
        return {"Cookie": cookie}
    return {}


def _mutates_state(tool_name: str) -> bool:
    tool = get_tool(tool_name)
    return bool(tool and tool.requires_approval)


def handle_request(line: str) -> dict:
    """Process one JSON-line MCP request."""
    req = json.loads(line)
    method = req.get("method", "")
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "subagent": t.subagent,
                    "requires_approval": t.requires_approval,
                }
                for t in list_tools()
            ]
        }
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        arguments = dict(params.get("arguments", {}))
        tool = get_tool(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}
        result = invoke_tool_http(name, arguments, ACCEL_URL, _headers())
        if result.get("error") == "accelerator_unreachable":
            return result
        audit_id = None
        if _mutates_state(name) and "error" not in result:
            bridge = IdeAuditBridge()
            audit_id = bridge.record(
                f"tool.{name}",
                profile_id=PROFILE_ID,
                metadata={"tool": name, "dry_run": arguments.get("dry_run", True)},
            )
        payload = {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
        }
        if audit_id:
            payload["audit_event_id"] = audit_id
        return payload
    return {"error": f"Unknown method: {method}"}


def main():
    """Minimal stdio MCP loop for Cursor integration."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_request(line)
            print(json.dumps(resp), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
