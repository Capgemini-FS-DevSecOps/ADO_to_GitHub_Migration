"""MCP server exposing Accelerator operations as tools."""
from __future__ import annotations

import json
import os
import sys

import httpx

ACCEL_URL = os.environ.get("ACCELERATOR_URL", "http://localhost:8080")


def _call(method: str, path: str, body: dict | None = None) -> dict:
    with httpx.Client(base_url=ACCEL_URL, timeout=120.0) as client:
        if method == "GET":
            r = client.get(path)
        else:
            r = client.post(path, json=body or {})
        r.raise_for_status()
        return r.json()


TOOLS = {
    "ado2gh_discover": lambda p: _call("POST", "/v1/discover", p),
    "ado2gh_readiness": lambda p: _call("POST", "/v1/pipeline-readiness", p),
    "ado2gh_plan_phase": lambda p: _call("POST", "/v1/plan", p),
    "ado2gh_enqueue_job": lambda p: _call("POST", "/v1/jobs", p),
    "ado2gh_job_status": lambda p: _call("GET", f"/v1/jobs/{p['job_id']}", None),
    "ado2gh_validate_repo": lambda p: _call("POST", "/v1/validate", p),
    "ado2gh_gate_check": lambda p: _call("GET", "/v1/dashboard", None),
    "ado2gh_get_report": lambda p: _call("GET", "/v1/dashboard", None),
}


def handle_request(line: str) -> dict:
    req = json.loads(line)
    method = req.get("method", "")
    if method == "tools/list":
        return {
            "tools": [
                {"name": name, "description": f"Call Accelerator {name}"}
                for name in TOOLS
            ]
        }
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        fn = TOOLS.get(name)
        if not fn:
            return {"error": f"Unknown tool: {name}"}
        return {"content": [{"type": "text", "text": json.dumps(fn(arguments), indent=2)}]}
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
