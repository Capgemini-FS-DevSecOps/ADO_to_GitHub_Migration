"""Session lifecycle routes: create, list, read, delete, provision, remediate."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
    is_admin_request,
    live_execution_block_message,
    request_username,
    session_requires_live_approval,
)
from ado2gh.agents.migration_agent.route_helpers import (
    ProvisionRequest,
    RemediateRequest,
    SessionRequest,
    _accel_get,
    _accel_post,
    _add_message,
    _assert_deployment_profile_active,
    _audit,
    _audit_actor,
    _build_migration_plan,
    _enqueue_session_live_approval,
    _get_accessible_session,
    _profile,
    _require_operate,
    _resolve_model_id,
    _runs,
    _session_payload,
    _session_token_from_request,
    _sessions,
)
from ado2gh.agents.migration_agent.runtime.orchestrator import (
    process_user_message,
)
from ado2gh.agents.migration_agent.session.state import (
    OrchestratorResult,
    set_session_idle,
)
from ado2gh.auth.service import auth_enabled

router = APIRouter()


@router.post("/v1/sessions")
async def create_session(req: SessionRequest, request: Request) -> dict[str, Any]:
    """Create a chat session; PEV runs only when execute_pev is true or /run-pev is called."""
    _require_operate(request)
    session_token = _session_token_from_request(request)
    await _assert_deployment_profile_active(req.profile_id)
    profile = _profile(req.profile_id)
    dry_run = req.dry_run if req.dry_run is not None else profile.dry_run_default
    from ado2gh.agents.migration_agent.session.lifecycle import (
        apply_model_selection,
        new_isolated_agent_session,
        persist_session_snapshot,
    )

    user_prompt = req.prompt or ""
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(req.model_id)
    actor, role = _audit_actor(request)
    platform_user = getattr(request.state, "platform_user", None)

    _sessions[session_id] = new_isolated_agent_session(
        session_id,
        profile_id=req.profile_id,
        session_token=session_token,
        dry_run=dry_run,
        user_username=getattr(platform_user, "username", None) if platform_user else None,
    )
    apply_model_selection(
        _sessions[session_id],
        selected_model_id=selected_model_id,
        llm_degraded=llm_degraded,
        llm_unconfigured=llm_unconfigured,
    )
    attach_actor_to_session(_sessions[session_id], platform_user)
    persist_session_snapshot(_sessions[session_id])

    if not user_prompt:
        return _session_payload(session_id)

    _add_message(session_id, "user", user_prompt, kind="message")

    if req.execute_pev and session_requires_live_approval(_sessions[session_id]):
        _add_message(
            session_id,
            "assistant",
            live_execution_block_message(_sessions[session_id]),
            kind="message",
        )
        try:
            await _enqueue_session_live_approval(_sessions[session_id])
        except Exception:
            pass
        _sessions[session_id]["live_approval_status"] = "pending"
        set_session_idle(_sessions[session_id])
        _audit.record(
            "session.start",
            profile_id=req.profile_id,
            actor=actor,
            session_id=session_id,
            metadata={"dry_run": dry_run, "role": role, "selected_model_id": selected_model_id},
        )
        return _session_payload(session_id)

    if llm_unconfigured:
        _add_message(
            session_id,
            "assistant",
            "No LLM models are configured. Please onboard a model in the LLM Catalog before using the agent.",
            kind="message",
        )
        return _session_payload(session_id)

    orch_degraded = False
    try:
        orch = await process_user_message(
            _sessions[session_id],
            user_prompt,
            deps={
                "accel_get": _accel_get,
                "accel_post": _accel_post,
                "build_plan": _build_migration_plan,
                "session_token": session_token,
            },
        )
    except (httpx.ConnectError, httpx.HTTPStatusError):
        orch = OrchestratorResult(tasks=_sessions[session_id].get("tasks", []))
        orch.reply = "Accelerator service not reachable. Session created in degraded mode."
        _add_message(session_id, "assistant", orch.reply, kind="message")
        orch_degraded = True
    if not orch_degraded:
        if orch.pending_form:
            _sessions[session_id]["pending_form"] = orch.pending_form
        if orch.reply:
            _add_message(session_id, "assistant", orch.reply, kind="message")

    _audit.record(
        "session.start",
        profile_id=req.profile_id,
        actor=actor,
        session_id=session_id,
        metadata={"dry_run": dry_run, "role": role, "selected_model_id": selected_model_id},
    )

    return _session_payload(session_id)


@router.get("/v1/sessions")
def list_sessions(request: Request, profile_id: str | None = None) -> dict[str, Any]:
    """List agent chat sessions, optionally filtered by deployment profile."""
    _require_operate(request)
    viewer = request_username(request)
    admin = is_admin_request(request)
    items_by_id: dict[str, dict[str, Any]] = {}

    def _maybe_add(sid: str, session: dict[str, Any]) -> None:
        if profile_id and session.get("profile_id") != profile_id:
            return
        if auth_enabled() and viewer and not admin:
            owner = session.get("user_username")
            if owner and owner != viewer:
                return
        first_user = next(
            (m for m in session.get("messages", []) if m.get("role") == "user"),
            None,
        )
        title = str((first_user or {}).get("content", "New chat"))[:80]
        items_by_id[sid] = {
            "session_id": sid,
            "profile_id": session.get("profile_id"),
            "title": title,
            "status": session.get("status"),
            "updated_at": session.get("updated_at"),
            "created_at": session.get("created_at"),
            "message_count": len(
                [m for m in session.get("messages", []) if m.get("kind") == "message"]
            ),
        }

    for sid, session in _sessions.items():
        _maybe_add(sid, session)

    try:
        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

        for row in MigrationSessionStore().list_sessions(profile_id):
            sid = str(row.get("session_id") or "")
            if not sid or sid in items_by_id:
                continue
            messages = json.loads(row.get("messages_json") or "[]")
            _maybe_add(
                sid,
                {
                    "profile_id": row.get("profile_id"),
                    "user_username": row.get("user_username"),
                    "messages": messages,
                    "status": row.get("status"),
                    "updated_at": row.get("last_activity_at"),
                    "created_at": row.get("created_at"),
                },
            )
    except Exception:
        pass

    items = list(items_by_id.values())
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"sessions": items}


@router.get("/v1/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """Return one chat session in full: messages, status, plan, and pending form."""
    _require_operate(request)
    _get_accessible_session(session_id, request)
    return _session_payload(session_id)


@router.delete("/v1/sessions/{session_id}")
def delete_session(session_id: str, request: Request) -> dict[str, str]:
    """Delete a chat session, cancel its run, and release its repository locks."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    from ado2gh.agents.migration_agent.session.lifecycle import (
        clear_session_migration_state,
        release_session_repo_locks,
    )

    run_id = session.get("run_id")
    if run_id and run_id in _runs:
        del _runs[run_id]
    clear_session_migration_state(session, current_run_id=str(run_id or "") or None)
    release_session_repo_locks(session_id)
    del _sessions[session_id]
    try:
        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

        MigrationSessionStore().delete_session(session_id)
    except Exception:
        pass
    return {"deleted": session_id}


@router.post("/v1/sessions/{session_id}/provision")
def provision_session(session_id: str, req: ProvisionRequest) -> dict[str, str]:
    """Set the provisioning tier; the write tier requires an Approver actor."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if req.tier == "write" and req.actor != "approver":
        raise HTTPException(status_code=403, detail="Write tier requires Approver")
    session["provision_tier"] = req.tier
    session["messages"].append({"role": req.actor, "content": f"provision:{req.tier}"})
    return {"session_id": session_id, "tier": req.tier, "status": "provisioned"}


@router.post("/v1/sessions/{session_id}/remediate")
async def remediate_session(session_id: str, req: RemediateRequest) -> dict[str, Any]:
    """Record a remediation attempt, escalating once the retry budget runs out."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    max_retries = int(os.environ.get("ADO2GH_MAX_RETRIES", "3"))
    if req.retry_count >= max_retries:
        session["status"] = "escalated"
        return {"session_id": session_id, "status": "escalated", "retry_count": req.retry_count}
    session["status"] = "remediating"
    return {"session_id": session_id, "status": "remediating", "retry_count": req.retry_count + 1}
