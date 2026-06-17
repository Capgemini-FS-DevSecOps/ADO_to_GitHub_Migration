"""Agent service — Planner-Executor-Validator loop with MCP tool exposure."""
from __future__ import annotations

import asyncio
import os
import uuid
import warnings
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ado2gh.agents.llm_provider import StubLLMProvider, get_llm_provider
from ado2gh.agents.local.audit_bridge import IdeAuditBridge
from ado2gh.agents.local.profiles import capability_matrix, get_profile
from ado2gh.agents.local.tool_catalog import TOOL_CATALOG_VERSION, list_tools
from ado2gh.agents.planner import AgentPlanner
from ado2gh.auth.service import AuthService, SESSION_COOKIE, auth_enabled, permissions_for

ACCEL_URL = os.environ.get("ACCELERATOR_URL", "http://accelerator:8080")

try:
    _ACTIVE_PROFILE = get_profile(os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight"))
except KeyError:
    _ACTIVE_PROFILE = None

app = FastAPI(title="ADO2GH Agent API", version="5.0.0")

_AGENT_AUTH_EXEMPT = {
    "/health",
    "/v1/llm/status",
    "/v1/llm-status",
    "/v1/mcp/tools",
}


@app.middleware("http")
async def agent_auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/v1/") or path in _AGENT_AUTH_EXEMPT:
        return await call_next(request)
    if path.startswith("/v1/internal/"):
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    session = AuthService().get_session(token)
    if not session:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    request.state.platform_user = session.user
    request.state.permissions = permissions_for(session.user.role)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_runs: dict[str, dict] = {}
_sessions: dict[str, dict] = {}
_approvals: dict[str, dict] = {}
_audit = IdeAuditBridge()


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
    assignment_id: Optional[str] = None
    profile_id: Optional[str] = None


class SessionRequest(BaseModel):
    profile_id: str = "lightweight"
    assignment_id: Optional[str] = None
    prompt: str = ""
    dry_run: bool = True
    model_id: Optional[str] = None


class SessionMessageRequest(BaseModel):
    message: str = ""


class ProvisionRequest(BaseModel):
    tier: str = "read"
    actor: str = "operator"
    reason: str = ""


class RemediateRequest(BaseModel):
    repo_key: str = ""
    retry_count: int = 0


class AgentRunResponse(BaseModel):
    run_id: str
    status: RunStatus
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str = ""


def _profile(profile_id: Optional[str] = None):
    pid = profile_id or os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight")
    try:
        return get_profile(pid)
    except KeyError:
        return _ACTIVE_PROFILE


def _accel_headers() -> dict:
    headers: dict[str, str] = {}
    cookie = os.environ.get("ADO2GH_SESSION_COOKIE", "")
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _platform_user(request: Request | None = None):
    if request is None:
        return None
    return getattr(request.state, "platform_user", None)


def _audit_actor(request: Request | None = None) -> tuple[str, str | None]:
    user = _platform_user(request)
    if user:
        return user.username, user.role.value
    return "local-developer", None


def _require_operate(request: Request) -> None:
    if not auth_enabled():
        return
    perms = getattr(request.state, "permissions", {})
    if not perms.get("can_operate"):
        raise HTTPException(status_code=403, detail="Missing capability: can_operate")


def _require_approve_live(request: Request) -> None:
    if not auth_enabled():
        return
    perms = getattr(request.state, "permissions", {})
    if not perms.get("can_approve_live_execution"):
        raise HTTPException(status_code=403, detail="Missing capability: can_approve_live_execution")


def _resolve_model_id(requested: str | None) -> tuple[str | None, bool]:
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    model_id = requested
    if not model_id:
        default = store.get_default_model()
        model_id = default.id if default else None
    provider = get_llm_provider(model_id)
    degraded = isinstance(provider, StubLLMProvider) and model_id is not None
    return model_id, degraded


async def _accel_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=120.0, headers=_accel_headers(),
    ) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _accel_get(path: str) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=60.0, headers=_accel_headers(),
    ) as client:
        r = await client.get(path)
        r.raise_for_status()
        return r.json()


async def _check_accelerator() -> tuple[bool, Optional[str]]:
    try:
        async with httpx.AsyncClient(base_url=ACCEL_URL, timeout=3.0) as client:
            r = await client.get("/health")
            return r.status_code == 200, None
    except httpx.ConnectError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


class FreshnessGuard:
    """Re-check ADO/GH HEAD SHA before write batches."""

    @staticmethod
    async def check(config_path: str, project: str, repo: str) -> dict:
        return await _accel_post("/v1/validate/freshness", {
            "config_path": config_path,
            "project": project,
            "repo": repo,
        })


async def pev_loop(run_id: str, req: AgentRunRequest, session_id: Optional[str] = None) -> None:
    run = _runs[run_id]
    steps: list[dict] = run["steps"]
    profile = _profile(req.profile_id)
    is_live = not req.dry_run

    try:
        if not is_live:
            run["status"] = RunStatus.PLANNING
            if session_id and session_id in _sessions:
                _sessions[session_id]["status"] = "planning"
                _sessions[session_id]["subagent"] = "planner"

            plan = await _accel_post("/v1/plan", {
                "config_path": req.config_path, "wave_id": req.wave_id,
            })
            steps.append({"phase": "plan", "tool": "ado2gh_plan_phase", "result": plan})
            _add_message(session_id, "planner", "Generated migration plan (dry-run)")

            readiness = await _accel_post("/v1/pipeline-readiness", {
                "config_path": req.config_path,
            })
            steps.append({"phase": "plan", "tool": "ado2gh_readiness", "result": readiness})

        run["status"] = RunStatus.EXECUTING
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "executing"
            _sessions[session_id]["subagent"] = "executor"

        migrate_body: dict[str, Any] = {
            "config_path": req.config_path,
            "wave_id": req.wave_id,
            "dry_run": req.dry_run,
        }
        if req.assignment_id:
            migrate_body["assignment_id"] = req.assignment_id
        if session_id and session_id in _sessions:
            approval_id = _sessions[session_id].get("live_approval_id")
            if approval_id:
                migrate_body["live_approval_id"] = approval_id

        migrate = await _accel_post("/v1/migrate", migrate_body)
        steps.append({"phase": "execute", "tool": "ado2gh_enqueue_job", "result": migrate})
        _add_message(
            session_id, "executor",
            "Enqueued migration jobs" if not is_live else "Live migration executed",
        )

        run["status"] = RunStatus.VALIDATING
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "validating"
            _sessions[session_id]["subagent"] = "validator"

        validation = await _accel_post("/v1/validate", {
            "config_path": req.config_path,
            "wave_id": req.wave_id,
        })
        steps.append({"phase": "validate", "tool": "ado2gh_validate_repo", "result": validation})
        _add_message(session_id, "validator", "Validation complete")

        run["status"] = RunStatus.COMPLETED
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "completed"
            _sessions[session_id]["subagent"] = None
            _sessions[session_id]["approval"] = {"approved": True} if is_live else None

    except Exception as exc:
        run["status"] = RunStatus.FAILED
        steps.append({"phase": "error", "error": str(exc)})
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "failed"
            _add_message(session_id, "system", f"Error: {exc}")


def _add_message(session_id: Optional[str], role: str, content: str) -> None:
    if not session_id or session_id not in _sessions:
        return
    _sessions[session_id]["messages"].append({"role": role, "content": content})
    _sessions[session_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    reachable, err = await _check_accelerator()
    profile = _profile()
    llm_provider = (
        profile.llm_provider if profile
        else os.environ.get("LLM_PROVIDER", "stub")
    )
    body: dict[str, Any] = {
        "status": "ok" if reachable else "degraded",
        "service": "agent",
        "accelerator_url": ACCEL_URL,
        "accelerator_reachable": reachable,
        "profile": profile.profile_id if profile else "lightweight",
        "llm_provider": llm_provider,
        "llm_degraded": not reachable or llm_provider == "stub",
        "auth_enabled": auth_enabled(),
        "tool_catalog_version": TOOL_CATALOG_VERSION,
        "remediation_steps": [],
    }
    if profile:
        body["capabilities"] = capability_matrix(profile)
    if not reachable:
        body["connection_error"] = err or "Connection refused"
        body["remediation_steps"] = [
            "Start accelerator: python -m uvicorn services.accelerator_api.main:app --port 8080",
            "Verify ADO2GH_SQLITE_PATH and ADO2GH_STORAGE_BACKEND=sqlite",
            "Check ACCELERATOR_URL matches running service",
            "Ensure browser sends session cookie (credentials: include)",
        ]
    return body


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
    """Deprecated — unified queue lives on accelerator /v1/platform/approvals."""
    warnings.warn(
        "In-memory agent approvals are deprecated; use GET /v1/platform/approvals",
        DeprecationWarning,
        stacklevel=2,
    )
    return []


async def _assert_deployment_profile_active(profile_id: str) -> None:
    """Reject agent sessions for non-active deployment profiles."""
    try:
        get_profile(profile_id)
        return
    except KeyError:
        pass
    try:
        data = await _accel_get(f"/v1/settings/profiles/{profile_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return
        raise HTTPException(status_code=exc.response.status_code, detail="profile_check_failed") from exc
    except Exception:
        return
    if data.get("status") != "active":
        raise HTTPException(status_code=403, detail="profile_not_active")


@app.post("/v1/sessions")
async def create_session(req: SessionRequest, request: Request):
    """Start PEV session with profile and dry-run default."""
    _require_operate(request)
    await _assert_deployment_profile_active(req.profile_id)
    profile = _profile(req.profile_id)
    dry_run = req.dry_run if req.dry_run is not None else profile.dry_run_default
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    selected_model_id, llm_degraded = _resolve_model_id(req.model_id)
    llm = get_llm_provider(selected_model_id)
    if isinstance(llm, StubLLMProvider) and not selected_model_id:
        llm_degraded = True
    planner = AgentPlanner()
    plan_preview = planner.plan(
        profile_id=req.profile_id,
        assignment_repos=[],
        dependency_edges=[],
        dry_run=dry_run,
    )
    llm_text = llm.complete(req.prompt or "Plan POC dry-run migration")
    actor, role = _audit_actor(request)

    _sessions[session_id] = {
        "session_id": session_id,
        "profile_id": req.profile_id,
        "assignment_id": req.assignment_id,
        "selected_model_id": selected_model_id,
        "llm_degraded": llm_degraded,
        "status": "planning",
        "subagent": "planner",
        "dry_run": dry_run,
        "messages": [
            {"role": "user", "content": req.prompt or ""},
            {"role": "planner", "content": llm_text},
        ],
        "plan_id": plan_preview.get("profile_id"),
        "run_id": None,
        "approval": None,
        "live_approval_id": None,
        "live_approval_status": None,
        "llm_available": not llm_degraded,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _audit.record(
        "session.start",
        profile_id=req.profile_id,
        actor=actor,
        session_id=session_id,
        assignment_id=req.assignment_id,
        metadata={"dry_run": dry_run, "role": role, "selected_model_id": selected_model_id},
    )

    agent_req = AgentRunRequest(
        dry_run=dry_run,
        assignment_id=req.assignment_id,
        profile_id=req.profile_id,
    )
    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "run_id": run_id,
        "session_id": session_id,
        "status": RunStatus.PLANNING,
        "request": agent_req.model_dump(),
        "steps": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _sessions[session_id]["run_id"] = run_id
    asyncio.create_task(pev_loop(run_id, agent_req, session_id))

    return {
        "session_id": session_id,
        "status": _sessions[session_id]["status"],
        "subagent": "planner",
        "dry_run": dry_run,
        "selected_model_id": selected_model_id,
        "llm_degraded": llm_degraded,
    }


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    run = _runs.get(session.get("run_id", ""))
    return {
        **session,
        "run_status": run.get("status") if run else None,
        "steps": run.get("steps", []) if run else [],
    }


@app.post("/v1/sessions/{session_id}/request-live")
async def request_live(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        approval = await _accel_post("/v1/platform/approvals", {
            "scope_type": "agent_session",
            "scope_id": session_id,
            "profile_id": session.get("profile_id"),
            "assignment_id": session.get("assignment_id"),
            "reason_request": "Request live PEV execution",
        })
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (401, 403):
            raise HTTPException(
                status_code=exc.response.status_code,
                detail="live_approval_enqueue_failed",
            ) from exc
        approval = None
    except httpx.HTTPError:
        approval = None

    if approval is None:
        session["status"] = "awaiting_approval"
        session["approval"] = {"required": True, "approved": False}
        _add_message(session_id, "system", "Live execution requested — awaiting approval")
        return {"session_id": session_id, "status": session["status"]}
    session["status"] = "awaiting_approval"
    session["live_approval_id"] = approval.get("id")
    session["approval"] = {"required": True, "approved": False, "approval_id": approval.get("id")}
    session["live_approval_status"] = approval.get("status", "pending")
    _add_message(session_id, "system", "Live execution requested — awaiting platform approval")
    return {
        "session_id": session_id,
        "status": session["status"],
        "live_approval_id": approval.get("id"),
    }


@app.post("/v1/sessions/{session_id}/approve")
async def approve_session(session_id: str, req: ApprovalRequest, request: Request):
    _require_approve_live(request)
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not req.approved:
        session["status"] = "completed"
        session["approval"] = {"approved": False, "reason": req.reason}
        _audit.record(
            "session.approve.denied",
            profile_id=session["profile_id"],
            session_id=session_id,
            outcome="denied",
            metadata={"reason": req.reason},
        )
        return {"session_id": session_id, "status": session["status"]}

    approval_id = session.get("live_approval_id")
    if approval_id:
        try:
            await _accel_post(
                f"/v1/platform/approvals/{approval_id}/approve",
                {"reason": req.reason},
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail="platform_approval_failed",
            ) from exc
        session["approval"] = {"approved": True, "reason": req.reason}
        session["live_approval_status"] = "approved"
        _audit.record(
            "session.approve",
            profile_id=session["profile_id"],
            session_id=session_id,
            metadata={"reason": req.reason, "approval_id": approval_id},
        )
        return {"session_id": session_id, "status": session.get("status", "executing")}

    session["dry_run"] = False
    session["approval"] = {"approved": True, "reason": req.reason}
    _audit.record(
        "session.approve",
        profile_id=session["profile_id"],
        session_id=session_id,
        metadata={"reason": req.reason},
    )
    run_id = session.get("run_id")
    if run_id and run_id in _runs:
        agent_req = AgentRunRequest(
            dry_run=False,
            assignment_id=session.get("assignment_id"),
            profile_id=session.get("profile_id"),
        )
        asyncio.create_task(pev_loop(run_id, agent_req, session_id))
    return {"session_id": session_id, "status": "executing"}


@app.post("/v1/internal/sessions/{session_id}/resume-live")
async def resume_live_internal(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session["dry_run"] = False
    session["live_approval_status"] = "approved"
    run_id = session.get("run_id")
    if not run_id or run_id not in _runs:
        run_id = str(uuid.uuid4())
        _runs[run_id] = {
            "run_id": run_id,
            "session_id": session_id,
            "status": RunStatus.PLANNING,
            "request": {},
            "steps": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        session["run_id"] = run_id
    agent_req = AgentRunRequest(
        dry_run=False,
        assignment_id=session.get("assignment_id"),
        profile_id=session.get("profile_id"),
    )
    asyncio.create_task(pev_loop(run_id, agent_req, session_id))
    return {"session_id": session_id, "status": "executing"}


@app.post("/v1/internal/sessions/{session_id}/deny-live")
def deny_live_internal(session_id: str, body: dict | None = None):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    reason = (body or {}).get("reason", "denied")
    session["status"] = "completed"
    session["live_approval_status"] = "denied"
    session["approval"] = {"approved": False, "reason": reason}
    _add_message(session_id, "system", f"Live execution denied: {reason}")
    return {"session_id": session_id, "status": session["status"]}


@app.post("/v1/sessions/{session_id}/message")
def session_message(session_id: str, req: SessionMessageRequest):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    llm = get_llm_provider()
    reply = llm.complete(req.message)
    session["messages"].append({"role": "user", "content": req.message})
    session["messages"].append({"role": "assistant", "content": reply})
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    return {"session_id": session_id, "reply": reply, "status": session["status"]}


@app.post("/v1/sessions/{session_id}/provision")
def provision_session(session_id: str, req: ProvisionRequest):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if req.tier == "write" and req.actor != "approver":
        raise HTTPException(status_code=403, detail="Write tier requires Approver")
    session["provision_tier"] = req.tier
    session["messages"].append({"role": req.actor, "content": f"provision:{req.tier}"})
    return {"session_id": session_id, "tier": req.tier, "status": "provisioned"}


@app.post("/v1/sessions/{session_id}/remediate")
async def remediate_session(session_id: str, req: RemediateRequest):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    max_retries = int(os.environ.get("ADO2GH_MAX_RETRIES", "3"))
    if req.retry_count >= max_retries:
        session["status"] = "escalated"
        return {"session_id": session_id, "status": "escalated", "retry_count": req.retry_count}
    run_id = session.get("run_id")
    if run_id and run_id in _runs:
        agent_req = AgentRunRequest(
            dry_run=session.get("dry_run", True),
            profile_id=session.get("profile_id"),
        )
        asyncio.create_task(pev_loop(run_id, agent_req, session_id))
    session["status"] = "remediating"
    return {"session_id": session_id, "status": "remediating", "retry_count": req.retry_count + 1}


@app.get("/v1/llm/status")
@app.get("/v1/llm-status")
def llm_status():
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    models = store.load()
    default = store.get_default_model()
    backend = (
        os.environ.get("LLM_PROVIDER")
        or os.environ.get("ADO2GH_LLM_BACKEND")
        or "stub"
    )
    unavailable = backend.lower() == "unavailable"
    return {
        "provider": backend,
        "available": not unavailable,
        "degraded": unavailable or not models,
        "message": "Using deterministic stub planner" if backend == "stub" else f"backend={backend}",
        "backend": backend,
        "models_configured": len([m for m in models if m.enabled]),
        "default_model_id": default.id if default else None,
    }


MCP_TOOLS = [
    {"name": t.name, "endpoint": f"{t.http_method} {t.http_path}"}
    for t in list_tools()
]


@app.get("/v1/mcp/tools")
def mcp_tools():
    return {"tools": MCP_TOOLS, "version": TOOL_CATALOG_VERSION}
