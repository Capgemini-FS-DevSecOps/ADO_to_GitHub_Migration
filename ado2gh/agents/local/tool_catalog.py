"""Shared migration tool catalog for HTTP sessions and MCP bridge."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import httpx

TOOL_CATALOG_VERSION = "1.0.0"


@dataclass(frozen=True)
class ToolContract:
    """Published tool definition for IDE and agent hosts."""

    name: str
    subagent: str
    http_method: str
    http_path: str
    description: str
    requires_approval: bool = False
    dry_run_default: bool = True


def _entry(
    name: str,
    subagent: str,
    method: str,
    path: str,
    description: str,
    requires_approval: bool = False,
) -> ToolContract:
    return ToolContract(
        name=name,
        subagent=subagent,
        http_method=method,
        http_path=path,
        description=description,
        requires_approval=requires_approval,
        dry_run_default=True,
    )


_CATALOG: Dict[str, ToolContract] = {
    "ado2gh_discover": _entry(
        "ado2gh_discover", "planner", "POST", "/v1/discover", "Read-only ADO org scan",
    ),
    "ado2gh_build_dependency_graph": _entry(
        "ado2gh_build_dependency_graph",
        "planner",
        "POST",
        "/v1/assignments/{assignment_id}/dependency-graph",
        "Topological sort and cycle detection",
    ),
    "ado2gh_plan_phase": _entry(
        "ado2gh_plan_phase", "planner", "POST", "/v1/plan", "Structured migration plan",
    ),
    "ado2gh_readiness": _entry(
        "ado2gh_readiness", "planner", "POST", "/v1/pipeline-readiness", "Pipeline readiness",
    ),
    "ado2gh_workflow_readiness": _entry(
        "ado2gh_workflow_readiness", "planner", "GET", "/v1/dashboard", "Workflow readiness checklist",
    ),
    "ado2gh_enqueue_job": _entry(
        "ado2gh_enqueue_job", "executor", "POST", "/v1/jobs", "Enqueue scoped migration job",
        requires_approval=True,
    ),
    "ado2gh_job_status": _entry(
        "ado2gh_job_status", "executor", "GET", "/v1/jobs/{job_id}", "Poll job status",
    ),
    "ado2gh_validate_repo": _entry(
        "ado2gh_validate_repo", "validator", "POST", "/v1/validate", "Scope validation",
    ),
    "ado2gh_gate_check": _entry(
        "ado2gh_gate_check", "validator", "GET", "/v1/assignments/{id}/gate-status", "Phase gates",
    ),
    "ado2gh_rollback": _entry(
        "ado2gh_rollback", "executor", "POST", "/v1/rollback", "Scope-targeted rollback",
        requires_approval=True,
    ),
    "ado2gh_get_report": _entry(
        "ado2gh_get_report", "planner", "GET", "/v1/dashboard", "Migration dashboard report",
    ),
}


def list_tools() -> list[ToolContract]:
    """Return all tools in stable order."""
    return list(_CATALOG.values())


def get_tool(name: str) -> Optional[ToolContract]:
    """Lookup tool by name."""
    return _CATALOG.get(name)


def executor_allowlist() -> frozenset[str]:
    """Executor-invokable tool names."""
    return frozenset(
        t.name for t in _CATALOG.values() if t.subagent in ("executor", "validator")
    )


def planner_allowlist() -> frozenset[str]:
    """Planner-invokable tool names."""
    return frozenset(t.name for t in _CATALOG.values() if t.subagent == "planner")


def _resolve_path(template: str, arguments: dict) -> str:
    path = template
    for key, value in arguments.items():
        path = path.replace(f"{{{key}}}", str(value))
    if "{" in path:
        path = path.replace("{id}", str(arguments.get("assignment_id", "")))
    return path


def default_arguments(tool: ToolContract, arguments: dict) -> dict:
    """Apply dry_run defaults for executor tools."""
    args = dict(arguments)
    if tool.requires_approval and "dry_run" not in args:
        args["dry_run"] = tool.dry_run_default
    if tool.name == "ado2gh_enqueue_job" and "payload" not in args:
        args["payload"] = {"dry_run": args.get("dry_run", True)}
    elif "dry_run" not in args and tool.requires_approval:
        args["dry_run"] = True
    return args


def invoke_tool_http(
    tool_name: str,
    arguments: dict,
    base_url: str,
    headers: Optional[dict] = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """
    Call accelerator HTTP API for a catalog tool.

    Returns JSON body or error dict with remediation_steps.
    """
    tool = get_tool(tool_name)
    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}

    args = default_arguments(tool, arguments)
    path = _resolve_path(tool.http_path, args)
    hdrs = headers or {}

    try:
        with httpx.Client(base_url=base_url, timeout=timeout) as client:
            if tool.http_method == "GET":
                r = client.get(path, headers=hdrs)
            else:
                body = {k: v for k, v in args.items() if k not in ("job_id", "assignment_id", "id")}
                if tool.name == "ado2gh_enqueue_job":
                    body = {
                        "job_type": args.get("job_type", "migrate"),
                        "payload": args.get("payload", args),
                        "idempotency_key": args.get("idempotency_key"),
                    }
                r = client.request(tool.http_method, path, json=body, headers=hdrs)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        return {
            "error": "accelerator_unreachable",
            "remediation_steps": [
                "Start accelerator: python -m uvicorn services.accelerator_api.main:app --port 8080",
                "Verify ACCELERATOR_URL matches running service",
                "Check ADO2GH_SQLITE_PATH and ADO2GH_STORAGE_BACKEND=sqlite",
            ],
        }
    except httpx.HTTPStatusError as exc:
        return {"error": f"http_{exc.response.status_code}", "detail": exc.response.text[:200]}


def build_mcp_handlers(base_url: str, headers: Optional[dict] = None) -> Dict[str, Callable]:
    """Map tool names to HTTP invokers for MCP server."""
    hdrs = headers or {}

    def _handler(args: dict) -> dict:
        name = args.pop("_tool_name", "")
        return invoke_tool_http(name, args, base_url, hdrs)

    handlers: Dict[str, Callable] = {}
    for name in _CATALOG:
        handlers[name] = lambda p, n=name: invoke_tool_http(n, p, base_url, hdrs)
    return handlers
