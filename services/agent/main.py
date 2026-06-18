"""Agent service — Planner-Executor-Validator loop with MCP tool exposure."""
from __future__ import annotations

import asyncio
import json
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

from ado2gh.agents.execution_mode import parse_execution_mode_from_message
from ado2gh.agents.live_execution_policy import (
    attach_actor_to_session,
    can_execute_live_without_approval,
    execution_policy_summary,
    live_execution_block_message,
    session_requires_live_approval,
)
from ado2gh.agents.llm_provider import StubLLMProvider, UnavailableLLMProvider, get_llm_provider
from ado2gh.agents.session_access import (
    assert_agent_session_access,
    is_admin_request,
    request_username,
)
from ado2gh.agents.failure_analysis import format_failure_feedback, format_retry_message
from ado2gh.agents.pev_coordinator import (
    MAX_PEV_RETRIES,
    review_executor_output,
    review_planner_output,
    review_validator_output,
    should_retry_pev,
)
from ado2gh.agents.local.audit_bridge import IdeAuditBridge
from ado2gh.agents.local.profiles import capability_matrix, get_profile
from ado2gh.agents.local.stub_llm import LocalStubLLM
from ado2gh.agents.local.tool_catalog import TOOL_CATALOG_VERSION, list_tools
from ado2gh.api.migration_work_plan import (
    build_work_items_for_repos,
    plan_narrative_from_work_items,
    work_items_summary,
)
from ado2gh.api.pipeline_runner import MIGRATE_UI_PIPELINE_STEPS
from ado2gh.auth.service import AuthService, SESSION_COOKIE, auth_enabled, permissions_for
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.state.factory import create_state_db

AGENT_DEFAULT_PIPELINE_STEPS = [s["id"] for s in MIGRATE_UI_PIPELINE_STEPS]
MIGRATE_STEP_IDS = frozenset({
    "migrate", "migrate_repos", "convert_pipelines", "map_secrets", "convert_metadata",
})

ACCEL_URL = os.environ.get("ACCELERATOR_URL", "http://accelerator:8080")

try:
    _ACTIVE_PROFILE = get_profile(os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight"))
except KeyError:
    _ACTIVE_PROFILE = None

app = FastAPI(title="ADO2GH Agent API", version="5.1.0")

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
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
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


class PlanPhaseBody(BaseModel):
    phase: str = "poc"


class SessionRequest(BaseModel):
    profile_id: str = "lightweight"
    assignment_id: Optional[str] = None
    prompt: str = ""
    dry_run: bool = True
    model_id: Optional[str] = None
    execute_pev: bool = False


class SessionMessageRequest(BaseModel):
    message: str = ""


class FormSubmitRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ExecutionModeRequest(BaseModel):
    dry_run: bool


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


_CHAT_SYSTEM = (
    "You are an ADO to GitHub migration assistant. "
    "Answer questions and help plan migrations in conversation. "
    "When summarizing a migration plan, list phase, repo count, and ordered steps clearly. "
    "Do not claim you executed migrations unless a pipeline run was explicitly started."
)

_PLANNER_SYSTEM = (
    "You are the migration planner subagent. Summarize the structured plan for an operator: "
    "target phase, repo count, execution order, dry-run vs live, and prerequisites."
)


def _profile(profile_id: Optional[str] = None):
    pid = profile_id or os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight")
    try:
        return get_profile(pid)
    except KeyError:
        return _ACTIVE_PROFILE


def _session_token_from_request(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.cookies.get(SESSION_COOKIE) or None


def _session_accel_token(session_id: str | None) -> str | None:
    if session_id and session_id in _sessions:
        token = _sessions[session_id].get("session_token")
        if token:
            return str(token)
    return None


def _accel_headers(session_token: str | None = None) -> dict[str, str]:
    if session_token:
        return {"Cookie": f"{SESSION_COOKIE}={session_token}"}
    env_cookie = os.environ.get("ADO2GH_SESSION_COOKIE", "").strip()
    if env_cookie:
        if "=" in env_cookie:
            return {"Cookie": env_cookie}
        return {"Cookie": f"{SESSION_COOKIE}={env_cookie}"}
    return {}


async def _accel_post(path: str, body: dict, *, session_token: str | None = None) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=120.0, headers=_accel_headers(session_token),
    ) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _accel_get(path: str, *, session_token: str | None = None) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=60.0, headers=_accel_headers(session_token),
    ) as client:
        r = await client.get(path)
        r.raise_for_status()
        return r.json()


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


def _llm_is_degraded(provider: object) -> bool:
    return isinstance(provider, (StubLLMProvider, LocalStubLLM))


def _llm_is_unconfigured(provider: object) -> bool:
    return isinstance(provider, UnavailableLLMProvider)


def _resolve_model_id(requested: str | None) -> tuple[str | None, bool, bool]:
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    model_id = requested
    if not model_id:
        default = store.get_default_model()
        model_id = default.id if default else None
    provider = get_llm_provider(model_id)
    unconfigured = _llm_is_unconfigured(provider)
    degraded = _llm_is_degraded(provider) or unconfigured
    return model_id, degraded, unconfigured


def _get_accessible_session(
    session_id: str,
    request: Request | None = None,
    *,
    profile_id: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    assert_agent_session_access(request, session, profile_id=profile_id, write=write)
    return session


def _session_payload(session_id: str) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    run = _runs.get(session.get("run_id", ""))
    return {
        **session,
        "run_status": run.get("status") if run else None,
        "steps": run.get("steps", []) if run else [],
        "tasks": session.get("tasks", []),
        "pending_form": session.get("pending_form"),
        "execution_policy": execution_policy_summary(session),
    }


async def _enqueue_session_live_approval(
    session_id: str,
    session: dict[str, Any],
    request: Request,
) -> dict[str, Any] | None:
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    try:
        approval = await _accel_post("/v1/platform/approvals", {
            "scope_type": "agent_session",
            "scope_id": session_id,
            "profile_id": session.get("profile_id"),
            "assignment_id": session.get("assignment_id"),
            "reason_request": "Request live PEV execution",
        }, session_token=session_token)
    except httpx.HTTPStatusError:
        approval = None
    except httpx.HTTPError:
        approval = None

    session["status"] = "awaiting_approval"
    session["approval"] = {"required": True, "approved": False}
    if approval:
        session["live_approval_id"] = approval.get("id")
        session["live_approval_status"] = approval.get("status", "pending")
        session["approval"]["approval_id"] = approval.get("id")
    return approval


async def _try_start_pev_run(session_id: str, request: Request | None = None) -> bool:
    """Start PEV when allowed. Returns False if live execution is blocked pending approval."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session_requires_live_approval(session):
        msg = live_execution_block_message(session)
        if request:
            await _enqueue_session_live_approval(session_id, session, request)
        _add_message(session_id, "assistant", msg, kind="message")
        return False
    _start_pev_run(session_id)
    return True


def _start_pev_run(session_id: str) -> str:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    plan = session.get("migration_plan")
    if not plan:
        raise HTTPException(
            status_code=409,
            detail="migration_plan_required — generate a plan before executing",
        )
    if plan.get("blocked"):
        raise HTTPException(status_code=409, detail=plan.get("block_reason", "plan_blocked"))
    if not session.get("plan_approved"):
        raise HTTPException(
            status_code=409,
            detail="plan_not_confirmed — review and confirm the migration plan before executing",
        )

    run_id = session.get("run_id")
    if run_id and run_id in _runs:
        existing = _runs[run_id]
        if existing.get("status") not in (RunStatus.COMPLETED, RunStatus.FAILED):
            raise HTTPException(status_code=409, detail="pev_already_running")

    agent_req = AgentRunRequest(
        dry_run=session.get("dry_run", True),
        assignment_id=session.get("assignment_id"),
        profile_id=session.get("profile_id"),
        phase=plan.get("phase", "poc"),
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
    session["run_id"] = run_id
    session["status"] = "planning"
    session["subagent"] = "planner"
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    asyncio.create_task(pev_loop(run_id, agent_req, session_id))
    return run_id


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


async def _build_migration_plan(
    session: dict[str, Any],
    session_token: str | None,
    *,
    phase: str = "poc",
) -> dict[str, Any]:
    """Profile discovery + work-item plan for executor/validator subagents."""
    from ado2gh.agents.planner import AgentPlanner

    profile_id = session.get("profile_id", "lightweight")
    dry_run = session.get("dry_run", True)
    repos: list[str] = []
    repo_configs: list[RepoConfig] = []
    deps: list[dict] = []
    gh_org = ""
    db_path: str | None = None

    try:
        settings = await _accel_get("/v1/settings", session_token=session_token)
        adv = settings.get("advanced") or {}
        db_path = adv.get("db_path")
        for prof in settings.get("migration_profiles") or []:
            if prof.get("id") == profile_id:
                gh_org = (prof.get("gh_org") or "").strip()
                break
    except httpx.HTTPStatusError:
        pass

    try:
        discovery = await _accel_get(
            f"/v1/settings/profiles/{profile_id}/discovery",
            session_token=session_token,
        )
        for r in discovery.get("repos", []):
            assigned = r.get("assigned_phase") or r.get("suggested_phase") or "poc"
            if assigned == phase:
                key = f"{r['project']}/{r['repo_name']}"
                repos.append(key)
                repo_configs.append(RepoConfig(
                    ado_project=r["project"],
                    ado_repo=r["repo_name"],
                    gh_org=(r.get("gh_org") or gh_org or "").strip(),
                    gh_repo=r.get("gh_repo") or r["repo_name"],
                    phase=phase,
                    risk_score=float(r.get("total_score") or 0),
                ))
    except httpx.HTTPStatusError:
        pass

    planner = AgentPlanner()
    plan = planner.plan(profile_id, repos, deps, dry_run=dry_run)
    plan["phase"] = phase
    plan["execution"] = "pipeline_run"
    plan["pipeline_steps"] = list(AGENT_DEFAULT_PIPELINE_STEPS)
    plan["repo_count"] = len(repos)
    plan["dry_run"] = dry_run

    db = None
    if db_path:
        try:
            db = create_state_db(db_path)
        except Exception:
            db = None

    inventory_count = 0
    if db:
        try:
            inventory_count = db.inventory_count()
        except Exception:
            inventory_count = 0
    if inventory_count == 0:
        try:
            await _accel_post(
                f"/v1/settings/profiles/{profile_id}/scan?sync=true",
                {},
                session_token=session_token,
            )
            if db:
                inventory_count = db.inventory_count()
            discovery = await _accel_get(
                f"/v1/settings/profiles/{profile_id}/discovery",
                session_token=session_token,
            )
            session["discovery_snapshot"] = discovery
        except httpx.HTTPStatusError:
            pass

    enabled_scopes = [
        MigrationScope.REPO.value,
        MigrationScope.PIPELINES.value,
        MigrationScope.SECRETS.value,
    ]
    work_items = build_work_items_for_repos(
        repo_configs,
        enabled_scopes=enabled_scopes,
        db=db,
    )
    mappings = session.get("operator_secret_mappings") or {}
    if mappings:
        from ado2gh.api.migration_work_plan import apply_operator_secret_mappings
        work_items = apply_operator_secret_mappings(work_items, mappings)
    plan["work_items"] = work_items
    plan["work_summary"] = work_items_summary(work_items)
    plan["narrative"] = plan_narrative_from_work_items(phase, work_items, dry_run=dry_run)

    if not repos:
        plan["blocked"] = True
        plan["block_reason"] = (
            "No repos in profile discovery for this phase. "
            "Run Discovery scan and assign phases, and ensure this profile is active."
        )
    return plan


async def _ensure_migration_plan(
    session: dict[str, Any],
    session_token: str | None,
    *,
    phase: str | None = None,
) -> dict[str, Any]:
    """Load discovery + planner output before PEV (deterministic, no LLM required)."""
    profile_id = session.get("profile_id", "lightweight")
    chosen_phase = phase or session.get("plan_phase", "poc")
    if not session.get("discovery_snapshot"):
        discovery = await _accel_get(
            f"/v1/settings/profiles/{profile_id}/discovery",
            session_token=session_token,
        )
        session["discovery_snapshot"] = discovery
        session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
    plan = session.get("migration_plan")
    if not plan:
        plan = await _build_migration_plan(session, session_token, phase=chosen_phase)
        session["migration_plan"] = plan
        session["plan_phase"] = chosen_phase
    return plan


def _sync_session_tasks(session_id: str | None, **updates: str) -> None:
    if not session_id or session_id not in _sessions:
        return
    tasks = _sessions[session_id].setdefault("tasks", [])
    if not tasks:
        from ado2gh.agents.session_orchestrator import _init_tasks
        tasks.extend(_init_tasks())
        _sessions[session_id]["tasks"] = tasks
    from ado2gh.agents.session_orchestrator import _set_task, sync_work_items_to_tasks
    for task_id, status in updates.items():
        _set_task(tasks, task_id, status)
    plan = _sessions[session_id].get("migration_plan") or {}
    if plan.get("work_items"):
        sync_work_items_to_tasks(_sessions[session_id], plan["work_items"])


def _sync_work_items_from_pipeline(session_id: str | None, final_run: dict[str, Any]) -> None:
    if not session_id or session_id not in _sessions:
        return
    from ado2gh.api.migration_work_plan import apply_scope_results_to_work_items
    from ado2gh.agents.session_orchestrator import sync_work_items_to_tasks

    session = _sessions[session_id]
    plan = session.get("migration_plan") or {}
    work_items = list(plan.get("work_items") or [])
    all_repo_details: list[dict[str, Any]] = []
    for step in final_run.get("steps", []):
        if step.get("id") not in MIGRATE_STEP_IDS:
            continue
        result = step.get("result") or {}
        if result.get("work_items"):
            work_items = list(result["work_items"])
        all_repo_details.extend(result.get("repo_details") or [])
    if work_items and all_repo_details:
        work_items = apply_scope_results_to_work_items(work_items, all_repo_details)
    if work_items:
        plan["work_items"] = work_items
        session["migration_plan"] = plan
        sync_work_items_to_tasks(session, work_items)


def _migration_steps_summary(final_run: dict[str, Any]) -> tuple[list[dict], bool]:
    """Return migrate-related steps and whether any failed."""
    steps = [
        s for s in final_run.get("steps", [])
        if s.get("id") in MIGRATE_STEP_IDS
    ]
    failed = any(s.get("status") == "failed" for s in steps)
    return steps, failed


async def _poll_pipeline_run(
    pipe_id: str,
    session_token: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    final_run: dict[str, Any] = {}
    for _ in range(180):
        await asyncio.sleep(2)
        polled = await _accel_get(
            f"/v1/pipeline/runs/{pipe_id}", session_token=session_token,
        )
        final_run = polled.get("run", polled)
        if session_id and session_id in _sessions:
            _sessions[session_id]["pipeline_run_id"] = pipe_id
        if final_run.get("status") in (
            "completed", "dry_run_complete", "failed", "cancelled",
        ):
            break
    return final_run


async def _start_agent_pipeline(
    plan: dict[str, Any],
    req: AgentRunRequest,
    *,
    is_live: bool,
    session_token: str | None,
) -> tuple[dict[str, Any], str]:
    run_body = {
        "name": f"Agent — {plan.get('phase', 'poc')}",
        "dry_run": not is_live,
        "phase": plan.get("phase", req.phase),
        "steps": plan.get("pipeline_steps", AGENT_DEFAULT_PIPELINE_STEPS),
    }
    pipeline_resp = await _accel_post(
        "/v1/pipeline/runs", run_body, session_token=session_token,
    )
    pipeline_run = pipeline_resp.get("run", pipeline_resp)
    return pipeline_run, pipeline_run["id"]


async def pev_loop(run_id: str, req: AgentRunRequest, session_id: Optional[str] = None) -> None:
    """Planner → executor → validator via LLM-reviewed pipeline runs (max 3 retries)."""
    run = _runs[run_id]
    steps: list[dict] = run["steps"]
    session = _sessions.get(session_id) if session_id else None
    session_token = _session_accel_token(session_id)
    plan = session.get("migration_plan") if session else None
    is_live = not (session.get("dry_run", True) if session else req.dry_run)
    model_id = session.get("selected_model_id") if session else None
    llm = get_llm_provider(model_id)
    _, llm_degraded, llm_unconfigured = _resolve_model_id(model_id)

    if is_live and session and session_requires_live_approval(session):
        run["status"] = RunStatus.AWAITING_APPROVAL
        steps.append({"phase": "error", "error": "live_approval_required"})
        _add_message(
            session_id,
            "assistant",
            live_execution_block_message(session),
            kind="message",
        )
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "awaiting_approval"
        return

    if not plan:
        run["status"] = RunStatus.FAILED
        steps.append({"phase": "error", "error": "migration_plan_required"})
        if session_id:
            _add_message(session_id, "system", "No migration plan — generate a plan first.")
        return

    if plan.get("blocked"):
        run["status"] = RunStatus.FAILED
        reason = plan.get("block_reason", "plan_blocked")
        steps.append({"phase": "error", "error": reason})
        _add_message(session_id, "system", reason)
        return

    try:
        run["status"] = RunStatus.PLANNING
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "planning"
            _sessions[session_id]["subagent"] = "planner"
        steps.append({"phase": "plan", "tool": "agent_planner", "result": plan})

        planner_review = review_planner_output(llm, plan, llm_degraded=llm_degraded)
        steps.append({
            "phase": "plan",
            "tool": "planner_llm_review",
            "result": planner_review.to_dict(),
        })
        _sync_session_tasks(session_id, plan="completed", discovery="completed")
        _add_message(session_id, "planner", planner_review.summary, kind="progress")
        if planner_review.next_action == "abort":
            run["status"] = RunStatus.FAILED
            if session_id and session_id in _sessions:
                _sessions[session_id]["status"] = "failed"
                _sessions[session_id]["subagent"] = None
            return

        try:
            readiness = await _accel_post(
                "/v1/pipeline-readiness",
                {"config_path": req.config_path},
                session_token=session_token,
            )
            steps.append({"phase": "plan", "tool": "ado2gh_readiness", "result": readiness})
        except Exception:
            pass

        attempt = 0
        final_run: dict[str, Any] = {}
        pipeline_failed = True
        migrate_step: dict[str, Any] | None = None
        validate_step: dict[str, Any] | None = None
        validator_review = None

        while attempt < MAX_PEV_RETRIES:
            attempt += 1
            if session_id and session_id in _sessions:
                _sessions[session_id]["pev_attempt"] = attempt
                _sessions[session_id]["status"] = "executing"
                _sessions[session_id]["subagent"] = "executor"

            run["status"] = RunStatus.EXECUTING
            _sync_session_tasks(
                session_id,
                execute="running",
            )
            if session_id and session_id in _sessions:
                for t in _sessions[session_id].get("tasks", []):
                    if t["id"] == "execute":
                        t["detail"] = f"Attempt {attempt}/{MAX_PEV_RETRIES}"

            pipeline_run, pipe_id = await _start_agent_pipeline(
                plan, req, is_live=is_live, session_token=session_token,
            )
            steps.append({
                "phase": "execute",
                "tool": "pipeline_run",
                "attempt": attempt,
                "result": {"run_id": pipe_id},
            })

            if pipeline_run.get("status") == "awaiting_approval":
                run["status"] = RunStatus.AWAITING_APPROVAL
                if session_id and session_id in _sessions:
                    _sessions[session_id]["status"] = "awaiting_approval"
                    _sessions[session_id]["pipeline_run_id"] = pipe_id
                _add_message(
                    session_id,
                    "executor",
                    "Pipeline awaiting platform approval for live run.",
                    kind="progress",
                )
                return

            final_run = await _poll_pipeline_run(pipe_id, session_token, session_id)
            steps.append({
                "phase": "execute",
                "tool": "pipeline_run_complete",
                "attempt": attempt,
                "result": final_run,
            })
            _sync_work_items_from_pipeline(session_id, final_run)

            executor_review = review_executor_output(
                llm, final_run, attempt, llm_degraded=llm_degraded,
            )
            steps.append({
                "phase": "execute",
                "tool": "executor_llm_review",
                "attempt": attempt,
                "result": executor_review.to_dict(),
            })
            _add_message(session_id, "executor", executor_review.summary, kind="progress")

            run["status"] = RunStatus.VALIDATING
            if session_id and session_id in _sessions:
                _sessions[session_id]["status"] = "validating"
                _sessions[session_id]["subagent"] = "validator"
            _sync_session_tasks(session_id, validate="running")

            validate_step = next(
                (s for s in final_run.get("steps", []) if s.get("id") == "validate"), None,
            )
            validator_review = review_validator_output(
                llm, final_run, validate_step, attempt, llm_degraded=llm_degraded,
            )
            steps.append({
                "phase": "validate",
                "tool": "validator_llm_review",
                "attempt": attempt,
                "result": validator_review.to_dict(),
            })
            steps.append({
                "phase": "validate",
                "tool": "pipeline_validate",
                "attempt": attempt,
                "result": validate_step or {},
            })
            _add_message(session_id, "validator", validator_review.summary, kind="progress")

            migrate_steps, migrate_failed = _migration_steps_summary(final_run)
            migrate_step = migrate_steps[0] if migrate_steps else None

            if validator_review.verdict == "pass" or validator_review.next_action == "proceed":
                if executor_review.verdict != "fail" and not migrate_failed:
                    pipeline_failed = False
                    _sync_session_tasks(session_id, execute="completed", validate="completed")
                    break

            if should_retry_pev(executor_review, validator_review, final_run, attempt):
                _add_message(
                    session_id,
                    "validator",
                    format_retry_message(
                        validator_review if validator_review.retry_recommended else executor_review,
                        final_run,
                        attempt,
                        max_retries=MAX_PEV_RETRIES,
                    ),
                    kind="progress",
                )
                continue

            pipeline_failed = (
                validator_review.verdict == "fail"
                or executor_review.verdict == "fail"
                or final_run.get("status") == "failed"
                or migrate_failed
            )
            exec_status = "failed" if pipeline_failed else "completed"
            val_status = "failed" if validator_review.verdict == "fail" else "completed"
            _sync_session_tasks(session_id, execute=exec_status, validate=val_status)
            break

        if pipeline_failed:
            run["status"] = RunStatus.FAILED
            if session_id and session_id in _sessions:
                _sessions[session_id]["status"] = "failed"
                _sessions[session_id]["subagent"] = None
            feedback = format_failure_feedback(
                final_run,
                fallback_summary=(
                    (validator_review.summary if validator_review else "")
                    or (migrate_step.get("message") if migrate_step else "")
                    or "Migration step failed."
                ),
            )
            _add_message(session_id, "validator", feedback)
        else:
            run["status"] = RunStatus.COMPLETED
            if session_id and session_id in _sessions:
                _sessions[session_id]["status"] = "completed"
                _sessions[session_id]["subagent"] = None

    except Exception as exc:
        run["status"] = RunStatus.FAILED
        steps.append({"phase": "error", "error": str(exc)})
        if session_id and session_id in _sessions:
            _sessions[session_id]["status"] = "failed"
            _sessions[session_id]["subagent"] = None
            _add_message(session_id, "system", f"Error: {exc}")


def _add_message(
    session_id: Optional[str],
    role: str,
    content: str,
    *,
    kind: str = "message",
) -> None:
    if not session_id or session_id not in _sessions:
        return
    _sessions[session_id]["messages"].append({
        "role": role,
        "content": content,
        "kind": kind,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _sessions[session_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    reachable, err = await _check_accelerator()
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(None)
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    enabled_models = store.list_agent_ready_models()
    profile = _profile()
    body: dict[str, Any] = {
        "status": "ok" if reachable and not llm_degraded else "degraded",
        "service": "agent",
        "accelerator_url": ACCEL_URL,
        "accelerator_reachable": reachable,
        "profile": profile.profile_id if profile else "lightweight",
        "selected_model_id": selected_model_id,
        "models_configured": len(enabled_models),
        "llm_degraded": llm_degraded,
        "llm_unconfigured": llm_unconfigured,
        "auth_enabled": auth_enabled(),
        "tool_catalog_version": TOOL_CATALOG_VERSION,
        "remediation_steps": [],
    }
    if profile:
        body["capabilities"] = capability_matrix(profile)
    if not reachable:
        body["connection_error"] = err or "Connection refused"
        body["remediation_steps"] = [
            "Start accelerator: docker compose up accelerator",
            "Verify ADO2GH_DATA_DIR is shared between accelerator and agent containers",
            "Check ACCELERATOR_URL matches running service",
            "Ensure browser sends session cookie (credentials: include)",
        ]
    elif llm_unconfigured:
        body["remediation_steps"] = [
            "Open Settings → LLM models and add a provider (OpenAI, Anthropic, or Ollama)",
            "Run Validate on the model, then enable it and mark as default for agent",
            "Start a new agent chat after at least one model is enabled",
        ]
    elif llm_degraded:
        body["remediation_steps"] = [
            "Add and validate an LLM model under Settings → LLM models",
            "Enable the model and optionally mark it as default for agent",
            "Rebuild agent/accelerator after changing docker-compose volume mounts",
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


async def _assert_deployment_profile_active(
    profile_id: str,
    *,
    session_token: str | None = None,
) -> None:
    """Reject agent sessions for non-active deployment profiles."""
    try:
        get_profile(profile_id)
        return
    except KeyError:
        pass
    try:
        data = await _accel_get(
            f"/v1/settings/profiles/{profile_id}",
            session_token=session_token,
        )
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
    """Create a chat session; PEV runs only when execute_pev is true or /run-pev is called."""
    _require_operate(request)
    session_token = _session_token_from_request(request)
    await _assert_deployment_profile_active(req.profile_id, session_token=session_token)
    profile = _profile(req.profile_id)
    dry_run = req.dry_run if req.dry_run is not None else profile.dry_run_default
    user_prompt = req.prompt or "Hello"
    parsed_mode = parse_execution_mode_from_message(user_prompt)
    if parsed_mode is not None:
        dry_run = parsed_mode
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(req.model_id)
    llm = get_llm_provider(selected_model_id)
    actor, role = _audit_actor(request)

    _sessions[session_id] = {
        "session_id": session_id,
        "profile_id": req.profile_id,
        "assignment_id": req.assignment_id,
        "session_token": session_token,
        "selected_model_id": selected_model_id,
        "llm_degraded": llm_degraded,
        "llm_unconfigured": llm_unconfigured,
        "status": "idle",
        "subagent": None,
        "dry_run": dry_run,
        "messages": [],
        "plan_id": None,
        "run_id": None,
        "approval": None,
        "live_approval_id": None,
        "live_approval_status": None,
        "llm_available": not llm_unconfigured,
        "migration_plan": None,
        "plan_phase": "poc",
        "discovery_snapshot": None,
        "discovery_fetched_at": None,
        "tasks": [],
        "pending_form": None,
        "plan_approved": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    attach_actor_to_session(_sessions[session_id], getattr(request.state, "platform_user", None))

    migrate_intent = any(
        w in user_prompt.lower()
        for w in ("migrate", "migration", "plan phase", "dry-run", "dry run", "execute")
    )
    if migrate_intent or req.execute_pev:
        from ado2gh.agents.session_orchestrator import process_user_message

        orch = await process_user_message(
            _sessions[session_id],
            user_prompt,
            llm=llm,
            llm_degraded=llm_degraded,
            llm_unconfigured=llm_unconfigured,
            accel_get=_accel_get,
            accel_post=_accel_post,
            build_plan=_build_migration_plan,
            session_token=session_token,
        )
        if orch.start_pev or (
            req.execute_pev
            and can_execute_live_without_approval(_sessions[session_id])
        ):
            plan = await _ensure_migration_plan(_sessions[session_id], session_token)
            if plan.get("blocked"):
                raise HTTPException(
                    status_code=409,
                    detail=plan.get("block_reason", "migration_plan_blocked"),
                )
            started = await _try_start_pev_run(session_id, request)
            if not started:
                _sessions[session_id]["status"] = "awaiting_approval"
        elif req.execute_pev and session_requires_live_approval(_sessions[session_id]):
            _add_message(
                session_id,
                "assistant",
                live_execution_block_message(_sessions[session_id]),
                kind="message",
            )
            try:
                await _enqueue_session_live_approval(session_id, _sessions[session_id], request)
            except Exception:
                pass
            _sessions[session_id]["status"] = "awaiting_approval"
    else:
        llm_text = llm.complete(user_prompt, system=_CHAT_SYSTEM)
        _sessions[session_id]["messages"] = [
            {"role": "user", "content": user_prompt, "kind": "message", "timestamp": datetime.now(timezone.utc).isoformat()},
            {"role": "assistant", "content": llm_text, "kind": "message", "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    _audit.record(
        "session.start",
        profile_id=req.profile_id,
        actor=actor,
        session_id=session_id,
        assignment_id=req.assignment_id,
        metadata={"dry_run": dry_run, "role": role, "selected_model_id": selected_model_id},
    )

    session = _sessions[session_id]
    return _session_payload(session_id)


@app.get("/v1/sessions")
def list_sessions(request: Request, profile_id: str | None = None):
    """List agent chat sessions, optionally filtered by deployment profile."""
    _require_operate(request)
    viewer = request_username(request)
    admin = is_admin_request(request)
    items: list[dict[str, Any]] = []
    for sid, session in _sessions.items():
        if profile_id and session.get("profile_id") != profile_id:
            continue
        if auth_enabled() and viewer and not admin:
            owner = session.get("user_username")
            if owner and owner != viewer:
                continue
        first_user = next(
            (m for m in session.get("messages", []) if m.get("role") == "user"),
            None,
        )
        title = str((first_user or {}).get("content", "New chat"))[:80]
        items.append({
            "session_id": sid,
            "profile_id": session.get("profile_id"),
            "title": title,
            "status": session.get("status"),
            "updated_at": session.get("updated_at"),
            "created_at": session.get("created_at"),
            "message_count": len(session.get("messages", [])),
        })
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"sessions": items}


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    _require_operate(request)
    _get_accessible_session(session_id, request)
    return _session_payload(session_id)


@app.delete("/v1/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    _require_operate(request)
    _get_accessible_session(session_id, request, write=True)
    run_id = _sessions[session_id].get("run_id")
    if run_id and run_id in _runs:
        del _runs[run_id]
    del _sessions[session_id]
    return {"deleted": session_id}


@app.post("/v1/sessions/{session_id}/plan")
async def create_migration_plan(
    session_id: str,
    request: Request,
    body: PlanPhaseBody | None = None,
):
    """Planner subagent: LLM + discovery → structured migration_plan on the session."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    if session.get("status") not in ("idle", "completed", "failed"):
        raise HTTPException(status_code=409, detail="session_busy")
    phase = (body.phase if body else None) or session.get("plan_phase", "poc")
    session["plan_phase"] = phase
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    plan = await _build_migration_plan(session, session_token, phase=phase)
    llm = get_llm_provider(session.get("selected_model_id"))
    if not plan.get("blocked"):
        plan["narrative"] = llm.complete(
            f"Summarize this migration plan for the operator:\n{json.dumps(plan, indent=2)}",
            system=_PLANNER_SYSTEM,
        )
    else:
        plan["narrative"] = plan.get("block_reason", "Plan blocked")
    session["migration_plan"] = plan
    session["plan_approved"] = False
    session["status"] = "idle"
    session["subagent"] = "planner"
    _add_message(session_id, "planner", plan["narrative"], kind="progress")
    from ado2gh.agents.session_orchestrator import _plan_confirmation_form, _plan_confirmation_reply

    if not plan.get("blocked"):
        session["pending_form"] = _plan_confirmation_form(session)
        _add_message(session_id, "assistant", _plan_confirmation_reply(session), kind="message")
    session["subagent"] = None
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _session_payload(session_id)


@app.post("/v1/sessions/{session_id}/run-pev")
async def run_pev(session_id: str, request: Request):
    """Start planner → executor → validator against the accelerator (dry-run by default)."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    plan = session.get("migration_plan")
    if not plan:
        raise HTTPException(
            status_code=409,
            detail="migration_plan_required — generate a plan before executing",
        )
    if plan.get("blocked"):
        raise HTTPException(
            status_code=409,
            detail=plan.get("block_reason", "migration_plan_blocked"),
        )
    started = await _try_start_pev_run(session_id, request)
    if not started:
        payload = _session_payload(session_id)
        return {
            "session_id": session_id,
            "status": payload["status"],
            "subagent": payload.get("subagent"),
            "dry_run": payload.get("dry_run", True),
            "run_id": payload.get("run_id"),
            "blocked": True,
            "execution_policy": payload.get("execution_policy"),
        }
    payload = _session_payload(session_id)
    return {
        "session_id": session_id,
        "status": payload["status"],
        "subagent": payload.get("subagent"),
        "dry_run": payload.get("dry_run", True),
        "run_id": payload.get("run_id"),
    }


@app.post("/v1/sessions/{session_id}/request-live")
async def request_live(session_id: str, request: Request):
    session = _get_accessible_session(session_id, request)
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    approval = await _enqueue_session_live_approval(session_id, session, request)
    if approval is None:
        _add_message(session_id, "system", "Live execution requested — awaiting approval")
        return {"session_id": session_id, "status": session["status"]}
    _add_message(session_id, "system", "Live execution requested — awaiting platform approval")
    return {
        "session_id": session_id,
        "status": session["status"],
        "live_approval_id": approval.get("id"),
    }


@app.post("/v1/sessions/{session_id}/approve")
async def approve_session(session_id: str, req: ApprovalRequest, request: Request):
    _require_approve_live(request)
    session = _get_accessible_session(session_id, request)
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
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    if approval_id:
        try:
            await _accel_post(
                f"/v1/platform/approvals/{approval_id}/approve",
                {"reason": req.reason},
                session_token=session_token,
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
async def session_message(session_id: str, req: SessionMessageRequest, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    if session.get("status") not in ("idle", "completed", "failed"):
        raise HTTPException(
            status_code=409,
            detail="Cannot chat while PEV is running — wait for completion or start a new session",
        )
    if session.get("pending_form"):
        raise HTTPException(
            status_code=409,
            detail="pending_form — submit the form or cancel before sending a new message",
        )

    from ado2gh.agents.session_orchestrator import process_user_message

    llm = get_llm_provider(session.get("selected_model_id"))
    _, llm_degraded, llm_unconfigured = _resolve_model_id(session.get("selected_model_id"))
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)

    orch = await process_user_message(
        session,
        req.message,
        llm=llm,
        llm_degraded=llm_degraded,
        llm_unconfigured=llm_unconfigured,
        accel_get=_accel_get,
        accel_post=_accel_post,
        build_plan=_build_migration_plan,
        session_token=session_token,
    )

    if orch.start_pev:
        try:
            plan = await _ensure_migration_plan(session, session_token)
            if plan.get("blocked"):
                raise HTTPException(
                    status_code=409,
                    detail=plan.get("block_reason", "migration_plan_blocked"),
                )
            started = await _try_start_pev_run(session_id, request)
            if not started:
                session["status"] = "awaiting_approval"
        except HTTPException as exc:
            session["messages"].append({
                "role": "system",
                "content": str(exc.detail),
                "kind": "message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    session["status"] = "idle" if not orch.start_pev else session.get("status", "planning")
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _session_payload(session_id)
    payload["reply"] = orch.reply
    return payload


@app.post("/v1/sessions/{session_id}/form-submit")
async def submit_session_form(session_id: str, req: FormSubmitRequest, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    form = session.get("pending_form")
    if not form:
        raise HTTPException(status_code=409, detail="no_pending_form")

    from ado2gh.agents.session_orchestrator import migration_ready_reply, process_user_message

    values = req.values or {}
    form_id = str(form.get("form_id") or "")
    session["pending_form"] = None
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    reply = ""

    if form_id == "inventory_gaps":
        from ado2gh.agents.session_orchestrator import _plan_confirmation_form
        from ado2gh.api.migration_work_plan import apply_operator_secret_mappings, plan_narrative_from_work_items

        mappings = {
            str(k): str(v).strip()
            for k, v in values.items()
            if str(v).strip()
        }
        session["operator_secret_mappings"] = mappings
        plan = session.get("migration_plan") or {}
        work_items = plan.get("work_items") or []
        plan["work_items"] = apply_operator_secret_mappings(work_items, mappings)
        plan["work_summary"] = work_items_summary(plan["work_items"])
        plan["narrative"] = plan_narrative_from_work_items(
            plan.get("phase", session.get("plan_phase", "poc")),
            plan["work_items"],
            dry_run=session.get("dry_run", True),
        )
        session["migration_plan"] = plan
        form = _plan_confirmation_form(session)
        session["pending_form"] = form
        reply = (
            f"Saved {len(mappings)} service connection mapping(s). "
            "Review the migration plan and confirm when ready."
        )
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = reply
        payload["pending_form"] = form
        return payload

    if form_id == "plan_confirmation":
        plan_notes = str(values.get("plan_notes") or "").strip()
        plan_confirmed = bool(values.get("plan_confirmed"))
        confirm_execute = bool(values.get("confirm_execute"))

        if plan_notes and not plan_confirmed:
            session.pop("migration_plan", None)
            session["plan_approved"] = False
            _, replan_degraded, replan_unconfigured = _resolve_model_id(session.get("selected_model_id"))
            orch = await process_user_message(
                session,
                f"Revise the migration plan. Operator feedback: {plan_notes}",
                llm=get_llm_provider(session.get("selected_model_id")),
                llm_degraded=replan_degraded,
                llm_unconfigured=replan_unconfigured,
                accel_get=_accel_get,
                accel_post=_accel_post,
                build_plan=_build_migration_plan,
                session_token=session_token,
            )
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            payload = _session_payload(session_id)
            payload["reply"] = orch.reply or "Rebuilding migration plan with your changes…"
            if orch.pending_form:
                payload["pending_form"] = orch.pending_form
            return payload

        if not plan_confirmed:
            session["pending_form"] = form
            raise HTTPException(
                status_code=400,
                detail="Confirm the plan is correct, or describe changes in Notes without checking confirm.",
            )

        session["plan_approved"] = True
        plan = session.get("migration_plan") or {}
        if plan_notes:
            plan["operator_notes"] = plan_notes
            session["migration_plan"] = plan

        if confirm_execute:
            if plan.get("blocked"):
                raise HTTPException(
                    status_code=409,
                    detail=plan.get("block_reason", "migration_plan_blocked"),
                )
            started = await _try_start_pev_run(session_id, request)
            mode = "dry-run" if session.get("dry_run", True) else "live"
            if started:
                reply = plan.get("narrative") or f"Plan confirmed — starting {mode} migration…"
            else:
                reply = live_execution_block_message(session)
                session["status"] = "awaiting_approval"
        else:
            reply = migration_ready_reply(session)
            session["status"] = "idle"
            session["subagent"] = None

        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = reply
        return payload

    phase = str(values.get("phase") or session.get("plan_phase") or "poc").strip()
    if not phase:
        raise HTTPException(status_code=400, detail="phase_required")
    confirm_execute = bool(values.get("confirm_execute"))
    session["plan_phase"] = phase

    try:
        plan = await _ensure_migration_plan(session, session_token, phase=phase)
        if plan.get("blocked"):
            raise HTTPException(
                status_code=409,
                detail=plan.get("block_reason", "migration_plan_blocked"),
            )

        if confirm_execute:
            started = await _try_start_pev_run(session_id, request)
            mode = "dry-run" if session.get("dry_run", True) else "live"
            if started:
                reply = plan.get("narrative") or f"Starting {mode} migration for phase {phase}…"
            else:
                reply = live_execution_block_message(session)
                session["status"] = "awaiting_approval"
        else:
            reply = migration_ready_reply(session)
            session["status"] = "idle"
            session["subagent"] = None

        _add_message(session_id, "assistant", reply, kind="message")
    except HTTPException:
        session["pending_form"] = form
        raise
    except Exception as exc:
        session["pending_form"] = form
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _session_payload(session_id)
    payload["reply"] = reply
    return payload


@app.patch("/v1/sessions/{session_id}/execution-mode")
async def update_execution_mode(
    session_id: str, req: ExecutionModeRequest, request: Request,
):
    """Set dry-run vs live on an agent session (admins/approvers use live without approval queue)."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    if session.get("status") not in ("idle", "completed", "failed", "awaiting_approval"):
        raise HTTPException(status_code=409, detail="session_busy")
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    previous = bool(session.get("dry_run", True))
    session["dry_run"] = req.dry_run
    if previous != req.dry_run:
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        if not req.dry_run:
            session.pop("live_approval_status", None)
            session.pop("live_approval_id", None)
            if can_execute_live_without_approval(session):
                session["status"] = "idle"
                session.pop("approval", None)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    mode = "dry-run" if req.dry_run else "live"
    _add_message(
        session_id,
        "system",
        f"Execution mode set to **{mode}**."
        + (" Migration plan cleared — send a message to rebuild with the new mode." if previous != req.dry_run else ""),
        kind="message",
    )
    return _session_payload(session_id)


@app.post("/v1/sessions/{session_id}/form-cancel")
def cancel_session_form(session_id: str, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    session["pending_form"] = None
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _session_payload(session_id)


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
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(None)
    enabled = store.list_agent_ready_models()
    return {
        "provider": default.provider if default else None,
        "available": not llm_unconfigured,
        "degraded": llm_degraded,
        "unconfigured": llm_unconfigured,
        "message": (
            "No LLM models configured — add one under Settings → LLM models"
            if llm_unconfigured
            else ("Using deterministic stub planner" if llm_degraded else f"model={selected_model_id}")
        ),
        "backend": default.provider if default else None,
        "models_configured": len(enabled),
        "default_model_id": default.id if default else None,
        "selected_model_id": selected_model_id,
    }


MCP_TOOLS = [
    {"name": t.name, "endpoint": f"{t.http_method} {t.http_path}"}
    for t in list_tools()
]


@app.get("/v1/mcp/tools")
def mcp_tools():
    return {"tools": MCP_TOOLS, "version": TOOL_CATALOG_VERSION}
