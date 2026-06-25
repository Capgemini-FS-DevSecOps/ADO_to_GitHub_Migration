"""Shared utilities for agent service route handlers.

This module contains helpers migrated from services/agent/routes/_shared.py
that are needed by route handlers but not specific to the LangGraph agent logic.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.session_state import normalize_session_status
from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
    can_execute_live_without_approval,
    execution_policy_summary,
    live_execution_block_message,
    request_username,
    session_requires_live_approval,
    assert_agent_session_access,
    is_admin_request,
)
from ado2gh.agents.migration_agent.constants import TOOL_CATALOG_VERSION, MAX_PEV_RETRIES
from ado2gh.agents.migration_agent.utils import IdeAuditBridge, mask_secrets
from ado2gh.agents.migration_agent.tool_catalog import list_tools
from services.agent.profiles import capability_matrix, get_profile
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

_runs: dict[str, dict] = {}
# DEPRECATED: This in-memory session store will be replaced by the persistent
# SessionStore (ado2gh.agents.session_store.SessionStore) in Spec 011.
# Do not add new consumers; existing routes will be migrated incrementally.
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
    phase: Optional[str] = None
    wave_id: Optional[int] = None
    dry_run: bool = True
    assignment_id: Optional[str] = None
    profile_id: Optional[str] = None


class PlanPhaseBody(BaseModel):
    phase: Optional[str] = None


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


async def _accel_post_impl(path: str, body: dict, *, session_token: str | None = None) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=120.0, headers=_accel_headers(session_token),
    ) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _accel_get_impl(path: str, *, session_token: str | None = None) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=60.0, headers=_accel_headers(session_token),
    ) as client:
        r = await client.get(path)
        r.raise_for_status()
        return r.json()


async def _accel_request_impl(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    session_token: str | None = None,
) -> dict:
    """HTTP request against the accelerator (GET/POST/PATCH/PUT/DELETE)."""
    method_upper = method.upper()
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=120.0, headers=_accel_headers(session_token),
    ) as client:
        if method_upper == "GET":
            r = await client.get(path)
        elif method_upper == "POST":
            r = await client.post(path, json=body or {})
        elif method_upper == "PATCH":
            r = await client.patch(path, json=body or {})
        elif method_upper == "PUT":
            r = await client.put(path, json=body or {})
        elif method_upper == "DELETE":
            r = await client.delete(path)
        else:
            raise ValueError(f"unsupported_method: {method}")
        r.raise_for_status()
        if r.content:
            return r.json()
        return {"status_code": r.status_code, "ok": r.is_success}


async def _accel_post(path: str, body: dict, *, session_token: str | None = None) -> dict:
    """Lazy wrapper — picks up patches on services.agent.main."""
    import sys
    _m = sys.modules.get("services.agent.main")
    if _m and hasattr(_m, "_accel_post"):
        return await _m._accel_post(path, body, session_token=session_token)
    return await _accel_post_impl(path, body, session_token=session_token)


async def _accel_get(path: str, *, session_token: str | None = None) -> dict:
    """Lazy wrapper — picks up patches on services.agent.main."""
    import sys
    _m = sys.modules.get("services.agent.main")
    if _m and hasattr(_m, "_accel_get"):
        return await _m._accel_get(path, session_token=session_token)
    return await _accel_get_impl(path, session_token=session_token)


def _platform_user(request: Request | None = None):
    if request is None:
        return None
    return request_username(request)


def _audit_actor(request: Request | None = None) -> tuple[str, str | None]:
    if request is None:
        return "system", None
    actor = _platform_user(request)
    role = None
    if auth_enabled():
        permissions = permissions_for(request)
        if permissions:
            role = permissions.get("role")
    return actor, role


def _require_operate(request: Request) -> None:
    """Require operate permission."""
    if auth_enabled():
        from ado2gh.auth.service import permissions_for
        user = getattr(request.state, "platform_user", None) if request else None
        perms = permissions_for(user.role) if user else {}
        if not perms.get("can_operate", False):
            raise HTTPException(status_code=403, detail="operate_permission_required")


def _require_approve_live(request: Request) -> None:
    """Require approve_live permission."""
    if auth_enabled():
        from ado2gh.auth.service import permissions_for
        user = getattr(request.state, "platform_user", None) if request else None
        perms = permissions_for(user.role) if user else {}
        if not perms.get("can_approve_live_execution", False):
            raise HTTPException(status_code=403, detail="approve_live_permission_required")


def _resolve_model_id(requested: str | None) -> tuple[str | None, bool, bool]:
    """Resolve model ID and return (model_id, degraded, unconfigured)."""
    from ado2gh.agents.migration_agent.llm_bridge import _get_model_config, resolve_langchain_llm

    if not requested:
        requested = os.environ.get("ADO2GH_DEFAULT_LLM_MODEL")

    cfg = _get_model_config(requested)
    resolved_id = cfg.id if cfg else requested
    llm, degraded, unconfigured, _ = resolve_langchain_llm(requested)
    if unconfigured:
        degraded = True
    return resolved_id, degraded, unconfigured


def _get_accessible_session(
    session_id: str,
    request: Request | None = None,
) -> dict[str, Any]:
    """Get session if accessible to current user."""
    if session_id not in _sessions:
        hydrated = _try_hydrate_session(session_id)
        if hydrated is None:
            raise HTTPException(status_code=404, detail="Session not found")
        _sessions[session_id] = hydrated

    if auth_enabled() and request:
        viewer = request_username(request)
        admin = is_admin_request(request)
        owner = _sessions[session_id].get("user_username")
        if viewer and not admin and owner and owner != viewer:
            raise HTTPException(status_code=404, detail="Session not found")

    return _sessions[session_id]


def _try_hydrate_session(session_id: str) -> dict[str, Any] | None:
    """Load a persisted HTTP session into memory without cross-session state."""
    try:
        from ado2gh.agents.migration_agent.session_lifecycle import new_isolated_agent_session
        from ado2gh.agents.migration_agent.session_store import MigrationSessionStore

        record = MigrationSessionStore().get_session(session_id)
        if not record:
            return None
        session = new_isolated_agent_session(
            session_id,
            profile_id=str(record.get("profile_id") or "lightweight"),
            dry_run=bool(record.get("dry_run", True)),
            selected_model_id=str(record.get("model_id") or "") or None,
        )
        session["messages"] = list(record.get("messages") or [])
        session["migration_plan"] = record.get("migration_plan")
        session["pending_form"] = record.get("pending_form")
        session["status"] = str(record.get("status") or "idle")
        session["created_at"] = record.get("created_at")
        session["updated_at"] = record.get("last_activity_at") or record.get("created_at")
        session["iteration_count"] = int(record.get("iteration_count", 0) or 0)
        session["pev_retry_count"] = int(record.get("pev_retry_count", 0) or 0)
        return session
    except Exception:
        return None


_THINKING_EVENT_KINDS = frozenset({
    "thinking", "status", "progress", "tool_call", "tool_result", "task_update",
})


def _thinking_log_for_current_turn(all_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return thinking/status events for the active user turn only."""
    last_user_idx = -1
    for i, message in enumerate(all_messages):
        if message.get("kind") == "message" and message.get("role") == "user":
            last_user_idx = i
    turn_messages = all_messages[last_user_idx + 1 :] if last_user_idx >= 0 else all_messages
    return [
        {
            "kind": message.get("kind"),
            "content": message.get("content", ""),
            "subagent": message.get("subagent") or "orchestrator",
            "meta": message.get("meta"),
            "timestamp": message.get("timestamp"),
        }
        for message in turn_messages
        if message.get("kind") in _THINKING_EVENT_KINDS
    ]


def _prune_stale_thinking_events(session: dict[str, Any]) -> None:
    """Remove thinking/status events from prior user turns in session storage."""
    messages = session.get("messages") or []
    last_user_idx = -1
    for i, message in enumerate(messages):
        if message.get("kind") == "message" and message.get("role") == "user":
            last_user_idx = i
    if last_user_idx < 0:
        return
    pruned: list[dict[str, Any]] = []
    for i, message in enumerate(messages):
        kind = message.get("kind", "message")
        if kind == "message":
            pruned.append(message)
        elif kind in _THINKING_EVENT_KINDS and i > last_user_idx:
            pruned.append(message)
    session["messages"] = pruned


def _session_payload(session_id: str) -> dict[str, Any]:
    """Build session response payload."""
    session = _sessions.get(session_id, {})
    # Only include message events in the chat log — thinking/status events
    # are streamed live via SSE but should not appear as chat messages.
    # PEV agents (planner/executor/validator) only emit thinking events.
    all_messages = session.get("messages", [])
    chat_messages = [m for m in all_messages if m.get("kind") == "message"]
    thinking_log = _thinking_log_for_current_turn(all_messages)
    raw_status = session.get("status", "idle")
    return {
        "session_id": session_id,
        "profile_id": session.get("profile_id"),
        "assignment_id": session.get("assignment_id"),
        "status": normalize_session_status(raw_status),
        "live_approval_status": session.get("live_approval_status"),
        "dry_run": session.get("dry_run", True),
        "selected_model_id": session.get("selected_model_id"),
        "llm_degraded": session.get("llm_degraded", False),
        "llm_unconfigured": session.get("llm_unconfigured", False),
        "llm_available": session.get("llm_available", True),
        "messages": chat_messages,
        "thinking_log": thinking_log,
        "migration_plan": session.get("migration_plan"),
        "run_id": session.get("run_id"),
        "pipeline_run_id": session.get("pipeline_run_id") or session.get("run_id"),
        "discovery_snapshot": session.get("discovery_snapshot"),
        "discovery_fetched_at": session.get("discovery_fetched_at"),
        "pending_form": session.get("pending_form"),
        "tasks": session.get("tasks", []),
        "execution_policy": execution_policy_summary(session),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
    }


def _add_message(
    session_id: str,
    role: str,
    content: str,
    *,
    kind: str = "message",
    subagent: str | None = None,
) -> None:
    """Add a message to the session."""
    if session_id not in _sessions:
        return
    message = {
        "role": role,
        "content": content,
        "kind": kind,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if subagent:
        message["subagent"] = subagent
    _sessions[session_id].setdefault("messages", []).append(message)
    _sessions[session_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


async def _assert_deployment_profile_active(
    profile_id: str,
    *,
    session_token: str | None = None,
) -> None:
    """Assert that the deployment profile is active."""
    profile = _profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    # Additional profile validation can be added here


async def _build_migration_plan(
    session: dict[str, Any],
    session_token: str | None,
    *,
    phase: str | None = None,
    repository_id: str | None = None,
) -> dict[str, Any]:
    """Build migration plan from discovery data in the session.

    If a specific repository_id is provided, build a single-repo plan.
    Otherwise, build a plan from all repos in the discovery snapshot.
    """
    from datetime import datetime, timezone

    dry_run = session.get("dry_run", True)
    discovery = session.get("discovery_snapshot") or {}
    repos_data = discovery.get("repos", []) if isinstance(discovery, dict) else []

    # Filter to the requested repo if specified
    if repository_id:
        repos_data = [
            r for r in repos_data
            if (r.get("id") == repository_id
                or r.get("name") == repository_id
                or r.get("repo_name") == repository_id
                or f"{r.get('project', '')}/{r.get('name', '')}" == repository_id
                or f"{r.get('project', '')}/{r.get('repo_name', '')}" == repository_id)
        ]
        if not repos_data:
            # No matching repo in discovery — validate against ADO API
            # Parse repository_id as "project/repo" or just "repo"
            if "/" in repository_id:
                project, repo_name = repository_id.split("/", 1)
            else:
                project = ""
                repo_name = repository_id

            # Try to validate via accelerator API
            try:
                if _accel_get:
                    if project:
                        result = await _accel_get(
                            f"/v1/ado/projects/{project}/repos/{repo_name}",
                            session_token=session_token,
                        )
                    else:
                        # If no project specified, we can't validate - return error
                        return {
                            "error": "repo_not_found",
                            "message": f"Repository '{repository_id}' not found in discovery data. Please specify as Project/RepoName format.",
                            "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                        }
                    if result and result.get("error"):
                        return {
                            "error": "repo_not_found",
                            "message": f"Repository '{repository_id}' not found in Azure DevOps.",
                            "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                        }
                    # Repo exists in ADO, add to plan
                    repos_data = [{"id": repository_id, "name": repository_id, "scopes": ["git", "pipelines"], "project": project}]
                else:
                    return {
                        "error": "repo_not_found",
                        "message": f"Repository '{repository_id}' not found in discovery data and accelerator unavailable for validation.",
                        "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                    }
            except Exception:
                return {
                    "error": "repo_not_found",
                    "message": f"Repository '{repository_id}' not found in discovery data.",
                    "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                }

    # Build work items aligned with migrate-tab pipeline scopes
    from ado2gh.agents.migration_agent.pipeline_plan import (
        finalize_agent_migration_plan,
        repo_config_from_discovery,
    )
    from ado2gh.agents.migration_agent.scope_executor import build_agent_work_items_for_session
    from ado2gh.api.migration_work_plan import build_work_items_for_repos

    repo_configs = [
        repo_config_from_discovery(r, session) for r in repos_data if isinstance(r, dict)
    ]
    work_items = build_agent_work_items_for_session(
        repo_configs,
        session,
        db=None,
    ) if repo_configs else []

    plan = {
        "repos": [{"id": r.get("id", r.get("repo_name", r.get("name", ""))), "name": r.get("repo_name", r.get("name", ""))} for r in repos_data],
        "work_items": work_items,
        "dry_run": dry_run,
        "assumptions": [
            "Plan built from discovery snapshot",
            "Pipeline follows Migrate tab: connect → analyze_deps → migrate_repos → convert_pipelines → validate",
        ],
        "blocked_items": [r for r in repos_data if r.get("blocked")],
        "revision": 1,
        "repo_count": len(repos_data),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    resolved_phase = (phase or session.get("plan_phase") or "").strip() or None
    if resolved_phase:
        plan["phase"] = resolved_phase
    if repository_id:
        session = {**session, "plan_repository_id": repository_id}
    return finalize_agent_migration_plan(plan, session)


async def _ensure_migration_plan(
    session: dict[str, Any],
    session_token: str | None,
) -> dict[str, Any]:
    """Ensure migration plan exists, build if needed."""
    plan = session.get("migration_plan")
    if not plan:
        plan = await _build_migration_plan(session, session_token)
        session["migration_plan"] = plan
    return plan


async def _enqueue_session_live_approval(
    session_id: str,
    session: dict[str, Any],
    request: Request,
) -> None:
    """Enqueue session for live execution approval."""
    from ado2gh.agents.migration_agent.session_state import set_session_idle

    session["live_approval_status"] = "pending"
    set_session_idle(session)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()


async def _check_accelerator() -> tuple[bool, str | None]:
    """Check if accelerator service is reachable (short timeout — must not block agent /health)."""
    import httpx

    try:
        async with httpx.AsyncClient(base_url=ACCEL_URL, timeout=3.0) as client:
            r = await client.get("/health")
            r.raise_for_status()
        return True, None
    except Exception as exc:
        return False, str(exc)


# Legacy PEV loop stubs for compatibility during migration
# These will be removed once all route handlers are fully migrated to orchestrator
async def _try_start_pev_run(session_id: str, request: Request | None = None) -> bool:
    """Start PEV when allowed. Returns False if live execution is blocked pending approval."""
    from ado2gh.agents.migration_agent.orchestrator import process_user_message
    
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session_requires_live_approval(session):
        msg = live_execution_block_message(session)
        if request:
            await _enqueue_session_live_approval(session_id, session, request)
        _add_message(session_id, "assistant", msg, kind="message")
        return False
    
    # Use orchestrator to start PEV
    session_token = _session_accel_token(session_id)
    selected_model_id = session.get("selected_model_id")
    result = await process_user_message(
        session,
        "Start migration execution",
        model_id=selected_model_id,
        accel_get=_accel_get,
        accel_post=_accel_post,
        build_plan=_build_migration_plan,
        session_token=session_token,
    )
    
    if result.reply:
        _add_message(session_id, "assistant", result.reply, kind="message")
    
    return True
