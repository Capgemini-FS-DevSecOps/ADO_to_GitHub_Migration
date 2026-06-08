"""Agent service — Planner-Executor-Validator loop with MCP tool exposure."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ACCEL_URL = os.environ.get("ACCELERATOR_URL", "http://accelerator:8080")

app = FastAPI(title="ADO2GH Agent API", version="5.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_runs: dict[str, dict] = {}
_approvals: dict[str, dict] = {}


class RunStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunRequest(BaseModel):
    config_path: str = "migration.yaml"
    phase: str = "poc"
    wave_id: Optional[int] = None
    dry_run: bool = True


class AgentRunResponse(BaseModel):
    run_id: str
    status: RunStatus
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str = ""


async def _accel_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=ACCEL_URL, timeout=120.0) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _accel_get(path: str) -> dict:
    async with httpx.AsyncClient(base_url=ACCEL_URL, timeout=60.0) as client:
        r = await client.get(path)
        r.raise_for_status()
        return r.json()


class FreshnessGuard:
    """Re-check ADO/GH HEAD SHA before write batches."""

    @staticmethod
    async def check(config_path: str, project: str, repo: str) -> dict:
        return await _accel_post("/v1/validate/freshness", {
            "config_path": config_path,
            "project": project,
            "repo": repo,
        })


async def pev_loop(run_id: str, req: AgentRunRequest) -> None:
    run = _runs[run_id]
    steps: list[dict] = run["steps"]

    try:
        # PLAN
        run["status"] = RunStatus.PLANNING
        plan = await _accel_post("/v1/plan", {"config_path": req.config_path, "wave_id": req.wave_id})
        steps.append({"phase": "plan", "tool": "ado2gh_plan_phase", "result": plan})

        readiness = await _accel_post("/v1/pipeline-readiness", {
            "config_path": req.config_path,
        })
        steps.append({"phase": "plan", "tool": "ado2gh_readiness", "result": readiness})

        # EXECUTE (dry-run by default)
        run["status"] = RunStatus.EXECUTING
        migrate = await _accel_post("/v1/migrate", {
            "config_path": req.config_path,
            "wave_id": req.wave_id,
            "dry_run": req.dry_run,
        })
        steps.append({"phase": "execute", "tool": "ado2gh_enqueue_job", "result": migrate})

        # VALIDATE
        run["status"] = RunStatus.VALIDATING
        validation = await _accel_post("/v1/validate", {
            "config_path": req.config_path,
            "wave_id": req.wave_id,
        })
        steps.append({"phase": "validate", "tool": "ado2gh_validate_repo", "result": validation})

        if not req.dry_run:
            run["status"] = RunStatus.AWAITING_APPROVAL
            _approvals[run_id] = {"required": True, "reason": "push-workflows gate"}
        else:
            run["status"] = RunStatus.COMPLETED

    except Exception as exc:
        run["status"] = RunStatus.FAILED
        steps.append({"phase": "error", "error": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "service": "agent"}


@app.post("/v1/runs", response_model=AgentRunResponse)
async def start_run(req: AgentRunRequest):
    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "run_id": run_id,
        "status": RunStatus.PLANNING,
        "request": req.model_dump(),
        "steps": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    import asyncio
    asyncio.create_task(pev_loop(run_id, req))
    return AgentRunResponse(run_id=run_id, status=RunStatus.PLANNING, steps=[])


@app.get("/v1/runs/{run_id}", response_model=AgentRunResponse)
def get_run(run_id: str):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return AgentRunResponse(
        run_id=run_id,
        status=run["status"],
        steps=run["steps"],
    )


@app.post("/v1/runs/{run_id}/approve")
async def approve_run(run_id: str, req: ApprovalRequest):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if not req.approved:
        run["status"] = RunStatus.FAILED
        run["steps"].append({"phase": "approval", "approved": False, "reason": req.reason})
        return {"run_id": run_id, "status": run["status"]}

    run["steps"].append({"phase": "approval", "approved": True, "reason": req.reason})
    run["status"] = RunStatus.COMPLETED
    _approvals.pop(run_id, None)
    return {"run_id": run_id, "status": run["status"]}


@app.get("/v1/approvals")
def list_approvals():
    return [
        {"run_id": rid, **info}
        for rid, info in _approvals.items()
    ]


# ── MCP-compatible tool definitions (JSON schema for Cursor) ───────────────

MCP_TOOLS = [
    {"name": "ado2gh_discover", "endpoint": "POST /v1/discover"},
    {"name": "ado2gh_readiness", "endpoint": "POST /v1/pipeline-readiness"},
    {"name": "ado2gh_plan_phase", "endpoint": "POST /v1/plan"},
    {"name": "ado2gh_enqueue_job", "endpoint": "POST /v1/jobs"},
    {"name": "ado2gh_job_status", "endpoint": "GET /v1/jobs/{id}"},
    {"name": "ado2gh_validate_repo", "endpoint": "POST /v1/validate"},
    {"name": "ado2gh_gate_check", "endpoint": "GET /v1/dashboard"},
    {"name": "ado2gh_get_report", "endpoint": "GET /v1/dashboard"},
]


@app.get("/v1/mcp/tools")
def mcp_tools():
    return {"tools": MCP_TOOLS}
