"""Session, migration plan, chat, and form route handlers for the agent service."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
    can_execute_live_without_approval,
    is_admin_request,
    is_out_of_scope_message,
    live_execution_block_message,
    request_username,
    scope_refusal_reply,
    session_requires_live_approval,
)
from ado2gh.agents.migration_agent.llm_bridge import resolve_langchain_llm
from ado2gh.agents.migration_agent.forms import (
    migration_ready_reply,
)
from ado2gh.agents.migration_agent.session_state import OrchestratorResult
from ado2gh.agents.migration_agent.session_state import (
    is_session_busy,
    normalize_session_status,
    set_session_idle,
)
from ado2gh.agents.migration_agent.orchestrator import process_user_message, stream_user_message
from ado2gh.agents.migration_agent.constants import SSE_HEARTBEAT_INTERVAL_SECONDS
from ado2gh.auth.service import auth_enabled

from ado2gh.agents.migration_agent.route_helpers import (
    AgentRunRequest,
    ApprovalRequest,
    ExecutionModeRequest,
    FormSubmitRequest,
    PlanPhaseBody,
    ProvisionRequest,
    RemediateRequest,
    SessionMessageRequest,
    SessionRequest,
    RunStatus,
    _accel_get,
    _accel_post,
    _add_message,
    _assert_deployment_profile_active,
    _audit,
    _audit_actor,
    _build_migration_plan,
    _ensure_migration_plan,
    _enqueue_session_live_approval,
    _get_accessible_session,
    _PLANNER_SYSTEM,
    _platform_user,
    _profile,
    _prune_stale_thinking_events,
    _require_approve_live,
    _require_operate,
    _resolve_model_id,
    _runs,
    _session_accel_token,
    _session_payload,
    _session_token_from_request,
    _sessions,
    _try_start_pev_run,
)
from ado2gh.api.migration_work_plan import work_items_summary

router = APIRouter()


def _reject_if_session_busy(session: dict[str, Any]) -> None:
    if is_session_busy(session.get("status")):
        raise HTTPException(
            status_code=409,
            detail="Agent is busy — wait for the current step to finish or cancel it",
        )


def _resolve_pending_form(session: dict[str, Any]) -> dict[str, Any] | None:
    """Return the active pending form, rebuilding from operator-input state if needed."""
    form = session.get("pending_form")
    if form:
        return form
    from ado2gh.agents.migration_agent.operator_input import (
        operator_input_to_form,
        pending_operator_input,
    )

    request = pending_operator_input(session)
    if request is None:
        return None
    form = operator_input_to_form(request)
    session["pending_form"] = form
    return form


def _validate_repo_against_discovery(repo_id: str, session: dict[str, Any]) -> str | None:
    """Validate repo_id against discovery data. Returns error message if invalid, None if valid."""
    from ado2gh.agents.migration_agent.utils import validate_repo_against_discovery

    return validate_repo_against_discovery(repo_id, session.get("discovery_snapshot"))


async def _ensure_repo_valid_for_migration(
    repo_id: str,
    session: dict[str, Any],
    session_token: str | None,
) -> str | None:
    """Load discovery if needed and validate the repository id."""
    from ado2gh.agents.migration_agent.utils import load_discovery_snapshot, validate_repo_against_discovery

    await load_discovery_snapshot(session, _accel_get, session_token=session_token)
    return validate_repo_against_discovery(repo_id, session.get("discovery_snapshot"))


@router.post("/v1/sessions")
async def create_session(req: SessionRequest, request: Request):
    """Create a chat session; PEV runs only when execute_pev is true or /run-pev is called."""
    _require_operate(request)
    session_token = _session_token_from_request(request)
    await _assert_deployment_profile_active(req.profile_id, session_token=session_token)
    profile = _profile(req.profile_id)
    dry_run = req.dry_run if req.dry_run is not None else profile.dry_run_default
    from ado2gh.agents.migration_agent.session_lifecycle import new_isolated_agent_session, persist_session_snapshot

    user_prompt = req.prompt or ""
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    selected_model_id, llm_degraded, llm_unconfigured = _resolve_model_id(req.model_id)
    actor, role = _audit_actor(request)
    platform_user = getattr(request.state, "platform_user", None)

    _sessions[session_id] = new_isolated_agent_session(
        session_id,
        profile_id=req.profile_id,
        assignment_id=req.assignment_id,
        session_token=session_token,
        selected_model_id=selected_model_id,
        llm_degraded=llm_degraded,
        llm_unconfigured=llm_unconfigured,
        dry_run=dry_run,
        user_username=getattr(platform_user, "username", None) if platform_user else None,
        user_display_name=getattr(platform_user, "display_name", None) if platform_user else None,
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
            await _enqueue_session_live_approval(session_id, _sessions[session_id], request)
        except Exception:
            pass
        _sessions[session_id]["live_approval_status"] = "pending"
        set_session_idle(_sessions[session_id])
        _audit.record(
            "session.start",
            profile_id=req.profile_id,
            actor=actor,
            session_id=session_id,
            assignment_id=req.assignment_id,
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
            model_id=selected_model_id,
            accel_get=_accel_get,
            accel_post=_accel_post,
            build_plan=_build_migration_plan,
            session_token=session_token,
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
        assignment_id=req.assignment_id,
        metadata={"dry_run": dry_run, "role": role, "selected_model_id": selected_model_id},
    )

    return _session_payload(session_id)


@router.get("/v1/sessions")
def list_sessions(request: Request, profile_id: str | None = None):
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
        from ado2gh.agents.migration_agent.session_store import MigrationSessionStore

        for row in MigrationSessionStore().list_sessions(profile_id):
            sid = str(row.get("session_id") or "")
            if not sid or sid in items_by_id:
                continue
            messages = json.loads(row.get("messages_json") or "[]")
            _maybe_add(
                sid,
                {
                    "profile_id": row.get("profile_id"),
                    "user_username": None,
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
def get_session(session_id: str, request: Request):
    _require_operate(request)
    _get_accessible_session(session_id, request)
    return _session_payload(session_id)


@router.delete("/v1/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    from ado2gh.agents.migration_agent.session_lifecycle import (
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
        from ado2gh.agents.migration_agent.session_store import MigrationSessionStore

        MigrationSessionStore().delete_session(session_id)
    except Exception:
        pass
    return {"deleted": session_id}


@router.post("/v1/sessions/{session_id}/plan")
async def create_migration_plan(
    session_id: str,
    request: Request,
    body: PlanPhaseBody | None = None,
):
    """Planner subagent: LLM + discovery → structured migration_plan on the session."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    if is_session_busy(session.get("status")):
        raise HTTPException(status_code=409, detail="session_busy")
    phase = (body.phase if body and body.phase else None) or session.get("plan_phase")
    if phase:
        session["plan_phase"] = phase
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    plan = await _build_migration_plan(session, session_token, phase=phase)
    if not plan.get("blocked"):
        try:
            llm = resolve_langchain_llm(session.get("selected_model_id"))
            from langchain_core.messages import HumanMessage, SystemMessage
            msgs = [SystemMessage(content=_PLANNER_SYSTEM), HumanMessage(content=f"Summarize this migration plan for the operator:\n{json.dumps(plan, indent=2)}")]
            plan["narrative"] = llm.invoke(msgs).content
        except Exception:
            repo_n = len(plan.get("work_items", []))
            if phase:
                plan["narrative"] = f"Migration plan for phase {phase} with {repo_n} repos."
            else:
                plan["narrative"] = f"Migration plan with {repo_n} repos."
    else:
        plan["narrative"] = plan.get("block_reason", "Plan blocked")
    session["migration_plan"] = plan
    session["plan_approved"] = False
    session["status"] = "idle"
    session["subagent"] = "planner"
    _add_message(session_id, "planner", plan["narrative"], kind="progress")

    if not plan.get("blocked"):
        session["pending_form"] = plan_confirmation_form(session)
        _add_message(session_id, "assistant", _plan_confirmation_reply(session), kind="message")
    session["subagent"] = None
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _session_payload(session_id)


@router.post("/v1/sessions/{session_id}/run-pev")
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


@router.post("/v1/sessions/{session_id}/request-live")
async def request_live(session_id: str, request: Request):
    session = _get_accessible_session(session_id, request)
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    await _enqueue_session_live_approval(session_id, session, request)
    _add_message(session_id, "system", "Live execution requested — awaiting approval")
    return _session_payload(session_id)


@router.post("/v1/sessions/{session_id}/approve")
async def approve_session(session_id: str, req: ApprovalRequest, request: Request):
    _require_approve_live(request)
    session = _get_accessible_session(session_id, request)
    if not req.approved:
        set_session_idle(session)
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
    return {"session_id": session_id, "status": "executing"}


@router.post("/v1/internal/sessions/{session_id}/resume-live")
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
    return {"session_id": session_id, "status": "executing"}


@router.post("/v1/internal/sessions/{session_id}/deny-live")
def deny_live_internal(session_id: str, body: dict | None = None):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    reason = (body or {}).get("reason", "denied")
    set_session_idle(session)
    session["live_approval_status"] = "denied"
    session["approval"] = {"approved": False, "reason": reason}
    _add_message(session_id, "system", f"Live execution denied: {reason}")
    return {"session_id": session_id, "status": session["status"]}


@router.post("/v1/sessions/{session_id}/message")
async def session_message(session_id: str, req: SessionMessageRequest, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    _reject_if_session_busy(session)
    if session.get("pending_form"):
        raise HTTPException(
            status_code=409,
            detail="pending_form — submit the form or cancel before sending a new message",
        )

    from ado2gh.agents.migration_agent.orchestrator import process_user_message

    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)

    _add_message(session_id, "user", req.message, kind="message")
    _prune_stale_thinking_events(session)

    # Reset session status before starting a new graph invocation
    session["status"] = "idle"
    session["pending_clarification"] = None
    session["start_pev"] = False
    session.pop("pev_max_retries_exhausted", None)
    session.pop("last_pev_outcome", None)

    orch = await process_user_message(
        session,
        req.message,
        model_id=session.get("selected_model_id"),
        accel_get=_accel_get,
        accel_post=_accel_post,
        build_plan=_build_migration_plan,
        session_token=session_token,
    )

    session["status"] = "idle"
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _session_payload(session_id)
    payload["reply"] = orch.reply
    if orch.pending_form:
        session["pending_form"] = orch.pending_form
        payload["pending_form"] = orch.pending_form
    return payload


@router.post("/v1/sessions/{session_id}/message-stream")
async def session_message_stream(session_id: str, req: SessionMessageRequest, request: Request):
    """Stream agent events (thinking, status, tool updates) via SSE using LangGraph."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    _reject_if_session_busy(session)
    if session.get("pending_form"):
        raise HTTPException(
            status_code=409,
            detail="pending_form — submit the form or cancel before sending a new message",
        )

    from ado2gh.agents.migration_agent.orchestrator import stream_user_message
    from ado2gh.agents.migration_agent.constants import SSE_HEARTBEAT_INTERVAL_SECONDS

    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)

    _add_message(session_id, "user", req.message, kind="message")
    _prune_stale_thinking_events(session)

    from ado2gh.agents.migration_agent.utils import _reset_turn_status_budget

    _reset_turn_status_budget(session)

    # Reset session status before starting a new graph invocation
    session["status"] = "idle"
    session["pending_clarification"] = None
    session["start_pev"] = False
    session.pop("pev_max_retries_exhausted", None)
    session.pop("last_pev_outcome", None)

    async def event_stream():
        last_heartbeat = asyncio.get_event_loop().time()
        try:
            async for event in stream_user_message(
                session,
                req.message,
                model_id=session.get("selected_model_id"),
                accel_get=_accel_get,
                accel_post=_accel_post,
                build_plan=_build_migration_plan,
                session_token=session_token,
            ):
                if event.get("__done__"):
                    reply = event.get("reply", "")
                    pending_form = event.get("pending_form")
                    done_payload = {"kind": "__done__", "reply": reply}
                    if pending_form:
                        done_payload["pending_form"] = pending_form
                    yield f"data: {json.dumps(done_payload, default=str)}\n\n"
                else:
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                # Heartbeat keepalive
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                    yield f"data: {json.dumps({'kind': 'heartbeat', 'content': ''}, default=str)}\n\n"
                    last_heartbeat = now
        except Exception as exc:
            session["status"] = "idle"
            yield f"data: {json.dumps({'kind': 'thinking', 'content': f'Error: {exc}', 'role': 'system', 'subagent': 'orchestrator'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': str(exc)}, default=str)}\n\n"
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/v1/sessions/{session_id}/form-submit")
async def submit_session_form(session_id: str, req: FormSubmitRequest, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    form = _resolve_pending_form(session)
    if not form:
        raise HTTPException(status_code=409, detail="no_pending_form")

    from ado2gh.agents.migration_agent.orchestrator import process_user_message
    from ado2gh.agents.migration_agent.intake import prepare_form_submission, format_form_submission_summary

    values = req.values or {}
    form_id = str(form.get("form_id") or "")
    form_title = str(form.get("title") or form_id)
    session["pending_form"] = None
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    reply = ""

    values_summary = format_form_submission_summary(values, form_id=form_id)
    _add_message(session_id, "user", f"{form_title} submitted — {values_summary}", kind="message")
    _prune_stale_thinking_events(session)

    async def _ensure_repo(repo_id: str) -> str | None:
        return await _ensure_repo_valid_for_migration(repo_id, session, session_token)

    outcome = await prepare_form_submission(
        session,
        form,
        values,
        ensure_repo_valid=_ensure_repo,
    )

    if outcome["status"] == "operator_remediate" and outcome.get("remediation") == "run_pipeline_inventory":
        profile_id = session.get("profile_id", "lightweight")
        try:
            await _accel_post(
                f"/v1/settings/profiles/{profile_id}/scan?sync=true",
                json={},
                session_token=session_token,
            )
            discovery = await _accel_get(
                f"/v1/settings/profiles/{profile_id}/discovery",
                session_token=session_token,
            )
            session["discovery_snapshot"] = discovery
            session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Pipeline inventory scan failed: {exc}") from exc
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        session["status"] = "idle"
        session["start_pev"] = False
        orch = await process_user_message(
            session,
            outcome["message"],
            model_id=session.get("selected_model_id"),
            accel_get=_accel_get,
            accel_post=_accel_post,
            build_plan=_build_migration_plan,
            session_token=session_token,
        )
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = orch.reply or "Pipeline inventory complete. Rebuilding migration plan…"
        if orch.pending_form:
            payload["pending_form"] = orch.pending_form
        return payload

    if outcome["status"] == "operator_replan":
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        session["status"] = "idle"
        session["start_pev"] = False
        orch = await process_user_message(
            session,
            outcome["message"],
            model_id=session.get("selected_model_id"),
            accel_get=_accel_get,
            accel_post=_accel_post,
            build_plan=_build_migration_plan,
            session_token=session_token,
        )
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = orch.reply or outcome["message"]
        if orch.pending_form:
            payload["pending_form"] = orch.pending_form
        return payload

    if outcome["status"] == "operator_input_continue":
        from ado2gh.agents.migration_agent.forms import sanitize_form, _plan_confirmation_reply
        from ado2gh.agents.migration_agent.intake import build_plan_review_form
        from ado2gh.agents.migration_agent.pipeline_plan import finalize_agent_migration_plan

        plan = session.get("migration_plan") or {}
        plan = finalize_agent_migration_plan(plan, session)
        session["migration_plan"] = plan
        form = sanitize_form(build_plan_review_form(session))
        session["pending_form"] = form
        reply = _plan_confirmation_reply(session)
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = reply
        payload["pending_form"] = form
        return payload

    if outcome["status"] == "operator_input_unresolved":
        session["pending_form"] = outcome.get("form") or form
        raise HTTPException(status_code=400, detail=outcome.get("reply", "Unresolved operator input"))

    if outcome["status"] == "inventory_gaps":
        from ado2gh.api.migration_work_plan import apply_operator_secret_mappings, plan_narrative_from_work_items

        mappings = {
            str(k): str(v).strip()
            for k, v in outcome["values"].items()
            if str(v).strip()
        }
        session["operator_secret_mappings"] = mappings
        plan = session.get("migration_plan") or {}
        work_items = plan.get("work_items") or []
        plan["work_items"] = apply_operator_secret_mappings(work_items, mappings)
        plan["work_summary"] = work_items_summary(plan["work_items"])
        from ado2gh.agents.migration_agent.pipeline_plan import resolve_migration_phase

        plan_phase = resolve_migration_phase(session, plan)
        plan["narrative"] = plan_narrative_from_work_items(
            plan_phase or "",
            plan["work_items"],
            dry_run=session.get("dry_run", True),
            repository_id=plan.get("repository_id"),
        )
        session["migration_plan"] = plan
        from ado2gh.agents.migration_agent.intake import build_plan_review_form
        from ado2gh.agents.migration_agent.forms import sanitize_form

        form = sanitize_form(build_plan_review_form(session))
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

    if outcome["status"] == "repo_invalid":
        validation_error = outcome["validation_error"]
        form = outcome["form"]
        session["pending_form"] = form
        set_session_idle(session)
        _add_message(session_id, "system", validation_error, kind="thinking", subagent="orchestrator")
        reply = validation_error
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = reply
        payload["pending_form"] = form
        return payload

    if outcome["status"] == "plan_revise":
        session["status"] = "idle"
        session["pending_clarification"] = None
        session["start_pev"] = False
        orch = await process_user_message(
            session,
            outcome["message"],
            model_id=session.get("selected_model_id"),
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

    if outcome["status"] == "plan_unconfirmed":
        session["pending_form"] = outcome["form"]
        raise HTTPException(status_code=400, detail=outcome["reply"])

    if outcome["status"] == "plan_blocked":
        raise HTTPException(status_code=409, detail=outcome["reply"])

    if outcome["status"] == "plan_execute":
        session["start_execution"] = True
        session.pop("pev_execution_completed", None)
        session.pop("pev_execution_started", None)
        started = await _try_start_pev_run(session_id, request)
        plan = session.get("migration_plan") or {}
        mode = "dry-run" if session.get("dry_run", True) else "live"
        if started:
            reply = plan.get("narrative") or f"Plan confirmed — starting {mode} migration…"
        else:
            reply = live_execution_block_message(session)
            session["live_approval_status"] = "pending"
            set_session_idle(session)
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = reply
        return payload

    if outcome["status"] == "plan_ready":
        reply = migration_ready_reply(session)
        session["status"] = "idle"
        session["subagent"] = None
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = reply
        return payload

    session["status"] = "idle"
    session["pending_clarification"] = None
    session["start_pev"] = False
    orch = await process_user_message(
        session,
        outcome["message"],
        model_id=session.get("selected_model_id"),
        accel_get=_accel_get,
        accel_post=_accel_post,
        build_plan=_build_migration_plan,
        session_token=session_token,
    )
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _session_payload(session_id)
    payload["reply"] = orch.reply or "Form submitted."
    if orch.pending_form:
        payload["pending_form"] = orch.pending_form
    return payload


@router.post("/v1/sessions/{session_id}/form-submit-stream")
async def submit_session_form_stream(session_id: str, req: FormSubmitRequest, request: Request):
    """Stream agent events via SSE after a form submission."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    form = _resolve_pending_form(session)
    if not form:
        raise HTTPException(status_code=409, detail="no_pending_form")

    from ado2gh.agents.migration_agent.intake import prepare_form_submission, format_form_submission_summary
    from ado2gh.agents.migration_agent.orchestrator import stream_user_message
    from ado2gh.agents.migration_agent.constants import SSE_HEARTBEAT_INTERVAL_SECONDS

    values = req.values or {}
    form_id = str(form.get("form_id") or "")
    form_title = str(form.get("title") or form_id)
    session["pending_form"] = None
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)

    values_summary = format_form_submission_summary(values, form_id=form_id)
    _add_message(session_id, "user", f"{form_title} submitted — {values_summary}", kind="message")
    _prune_stale_thinking_events(session)

    async def _ensure_repo(repo_id: str) -> str | None:
        return await _ensure_repo_valid_for_migration(repo_id, session, session_token)

    outcome = await prepare_form_submission(
        session,
        form,
        values,
        ensure_repo_valid=_ensure_repo,
    )

    if outcome["status"] == "repo_invalid":
        validation_error = outcome["validation_error"]
        error_form = outcome["form"]
        session["pending_form"] = error_form
        set_session_idle(session)
        _add_message(session_id, "system", validation_error, kind="thinking", subagent="orchestrator")
        _add_message(session_id, "assistant", validation_error, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def repo_error_stream():
            yield f"data: {json.dumps({'kind': 'thinking', 'content': validation_error, 'role': 'system', 'subagent': 'orchestrator'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': 'message', 'content': validation_error, 'role': 'assistant'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': validation_error, 'pending_form': error_form}, default=str)}\n\n"

        return StreamingResponse(
            repo_error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    synthetic_msg: str | None = None

    if outcome["status"] == "operator_remediate" and outcome.get("remediation") == "run_pipeline_inventory":
        profile_id = session.get("profile_id", "lightweight")
        try:
            await _accel_post(
                f"/v1/settings/profiles/{profile_id}/scan?sync=true",
                json={},
                session_token=session_token,
            )
            discovery = await _accel_get(
                f"/v1/settings/profiles/{profile_id}/discovery",
                session_token=session_token,
            )
            session["discovery_snapshot"] = discovery
            session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Pipeline inventory scan failed: {exc}") from exc
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        synthetic_msg = outcome["message"]
    elif outcome["status"] == "operator_input_continue":
        from ado2gh.agents.migration_agent.forms import sanitize_form, _plan_confirmation_reply
        from ado2gh.agents.migration_agent.intake import build_plan_review_form
        from ado2gh.agents.migration_agent.pipeline_plan import finalize_agent_migration_plan

        plan = session.get("migration_plan") or {}
        plan = finalize_agent_migration_plan(plan, session)
        session["migration_plan"] = plan
        review_form = sanitize_form(build_plan_review_form(session))
        session["pending_form"] = review_form
        reply = _plan_confirmation_reply(session)
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def operator_input_continue_stream():
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': 'form_request', 'content': review_form.get('title', ''), 'meta': review_form}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply, 'pending_form': review_form}, default=str)}\n\n"

        return StreamingResponse(
            operator_input_continue_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    elif outcome["status"] == "operator_replan":
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        synthetic_msg = outcome["message"]
    elif outcome["status"] == "plan_unconfirmed":
        pending = outcome["form"]
        session["pending_form"] = pending
        reply = outcome["reply"]

        async def confirm_error_stream():
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply, 'pending_form': pending}, default=str)}\n\n"

        return StreamingResponse(
            confirm_error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    if outcome["status"] == "plan_ready":
        reply = migration_ready_reply(session)
        session["status"] = "idle"
        session["subagent"] = None
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def confirmed_stream():
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply}, default=str)}\n\n"

        return StreamingResponse(
            confirmed_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    if outcome["status"] == "plan_blocked":
        blocked = outcome["reply"]

        async def blocked_stream():
            yield f"data: {json.dumps({'kind': 'message', 'role': 'system', 'content': blocked}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': blocked}, default=str)}\n\n"

        return StreamingResponse(
            blocked_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    if outcome["status"] == "plan_execute":
        session["start_execution"] = True
        session.pop("pev_execution_completed", None)
        session.pop("pev_execution_started", None)
        synthetic_msg = (
            f"Plan confirmed. Start the migration for repository "
            f"{session.get('plan_repository_id', '')}."
        )
    elif outcome["status"] == "plan_revise":
        synthetic_msg = outcome["message"]
    elif outcome["status"] == "inventory_gaps":
        from ado2gh.api.migration_work_plan import apply_operator_secret_mappings, plan_narrative_from_work_items
        from ado2gh.agents.migration_agent.intake import build_plan_review_form
        from ado2gh.agents.migration_agent.forms import sanitize_form

        mappings = {
            str(k): str(v).strip()
            for k, v in outcome["values"].items()
            if str(v).strip()
        }
        session["operator_secret_mappings"] = mappings
        plan = session.get("migration_plan") or {}
        work_items = plan.get("work_items") or []
        plan["work_items"] = apply_operator_secret_mappings(work_items, mappings)
        plan["work_summary"] = work_items_summary(plan["work_items"])
        from ado2gh.agents.migration_agent.pipeline_plan import resolve_migration_phase

        plan_phase = resolve_migration_phase(session, plan)
        plan["narrative"] = plan_narrative_from_work_items(
            plan_phase or "",
            plan["work_items"],
            dry_run=session.get("dry_run", True),
            repository_id=plan.get("repository_id"),
        )
        session["migration_plan"] = plan
        review_form = sanitize_form(build_plan_review_form(session))
        session["pending_form"] = review_form
        reply = (
            f"Saved {len(mappings)} service connection mapping(s). "
            "Review the migration plan and confirm when ready."
        )
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def inventory_stream():
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': 'form_request', 'content': review_form.get('title', ''), 'meta': review_form}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply, 'pending_form': review_form}, default=str)}\n\n"

        return StreamingResponse(
            inventory_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    elif synthetic_msg is None:
        synthetic_msg = outcome["message"]

    session["status"] = "idle"
    session["pending_clarification"] = None
    session["start_pev"] = False

    async def event_stream():
        last_heartbeat = asyncio.get_event_loop().time()
        try:
            async for event in stream_user_message(
                session,
                synthetic_msg,
                model_id=session.get("selected_model_id"),
                accel_get=_accel_get,
                accel_post=_accel_post,
                build_plan=_build_migration_plan,
                session_token=session_token,
            ):
                if event.get("__done__"):
                    reply = event.get("reply", "")
                    pending_form = event.get("pending_form")
                    done_payload = {"kind": "__done__", "reply": reply}
                    if pending_form:
                        done_payload["pending_form"] = pending_form
                    yield f"data: {json.dumps(done_payload, default=str)}\n\n"
                else:
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                    yield f"data: {json.dumps({'kind': 'heartbeat', 'content': ''}, default=str)}\n\n"
                    last_heartbeat = now
        except Exception as exc:
            session["status"] = "idle"
            yield f"data: {json.dumps({'kind': 'thinking', 'content': f'Error: {exc}', 'role': 'system', 'subagent': 'orchestrator'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': str(exc)}, default=str)}\n\n"
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/v1/sessions/{session_id}/execution-mode")
async def update_execution_mode(
    session_id: str, req: ExecutionModeRequest, request: Request,
):
    """Set dry-run vs live on an agent session (admins/approvers use live without approval queue)."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    if is_session_busy(session.get("status")):
        raise HTTPException(status_code=409, detail="session_busy")
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    previous = bool(session.get("dry_run", True))
    session["dry_run"] = req.dry_run
    if previous != req.dry_run:
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
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


@router.post("/v1/sessions/{session_id}/form-cancel")
def cancel_session_form(session_id: str, request: Request):
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    from ado2gh.agents.migration_agent.session_lifecycle import persist_session_snapshot
    from ado2gh.agents.migration_agent.session_state import reset_session_for_new_migration

    session["pending_form"] = None
    reset_session_for_new_migration(session)
    session["status"] = "idle"
    session["subagent"] = None
    session["tasks"] = []
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    persist_session_snapshot(session)
    _add_message(session_id, "user", "Form cancelled", kind="message")
    _add_message(session_id, "assistant", "Form cancelled. The migration plan has been cleared — describe what you'd like to do next.", kind="message")
    return _session_payload(session_id)


@router.post("/v1/sessions/{session_id}/provision")
def provision_session(session_id: str, req: ProvisionRequest):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if req.tier == "write" and req.actor != "approver":
        raise HTTPException(status_code=403, detail="Write tier requires Approver")
    session["provision_tier"] = req.tier
    session["messages"].append({"role": req.actor, "content": f"provision:{req.tier}"})
    return {"session_id": session_id, "tier": req.tier, "status": "provisioned"}


@router.post("/v1/sessions/{session_id}/remediate")
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
    session["status"] = "remediating"
    return {"session_id": session_id, "status": "remediating", "retry_count": req.retry_count + 1}


@router.get("/v1/agent/models")
def agent_models(request: Request):
    from ado2gh.api.agent_models import list_agent_models

    return list_agent_models()


@router.get("/v1/llm/status")
@router.get("/v1/llm-status")
def llm_status():
    from ado2gh.api.llm.llm_model_store import LLMModelStore

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


# ─── T062-T064: Hands-off migration MVP ───


@router.get("/v1/sessions/{session_id}/plan-summary")
def get_plan_summary(session_id: str, request: Request):
    """T063: Present plan summary (repos, scopes, order, risks) for user confirmation.

    Enforces dry-run default; live requires explicit confirmation (FR-014, FR-015, CA-001).
    """
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    plan = session.get("migration_plan")
    if not plan:
        raise HTTPException(status_code=404, detail="No migration plan found for this session")

    work_items = plan.get("work_items", [])
    # T076: Highlight destructive operations requiring individual confirmation (FR-054, CA-002)
    DESTRUCTIVE_SCOPES = {"repo_delete", "workflow_delete", "secret_delete", "pipeline_disable"}
    destructive_operations = []
    for wi in work_items:
        for scope in wi.get("scopes", []):
            if scope in DESTRUCTIVE_SCOPES:
                destructive_operations.append({
                    "repo": wi.get("repo", ""),
                    "scope": scope,
                    "requires_individual_confirmation": True,
                })
    summary = {
        "session_id": session_id,
        "dry_run": plan.get("dry_run", True),
        "repos": plan.get("repo_order", []),
        "work_items": [
            {
                "repo": wi.get("repo", ""),
                "scopes": wi.get("scopes", []),
                "status": wi.get("status", "ready"),
                "blocked_reasons": wi.get("blocked_reasons", []),
            }
            for wi in work_items
        ],
        "assumptions": plan.get("assumptions", []),
        "revision": plan.get("revision", 1),
        "requires_confirmation": not plan.get("dry_run", True),
        "destructive_operations": destructive_operations,
    }
    return summary


@router.post("/v1/sessions/{session_id}/confirm-live")
def confirm_live_execution(session_id: str, request: Request):
    """T063: Explicit user confirmation for live execution (CA-001)."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    plan = session.get("migration_plan")
    if not plan:
        raise HTTPException(status_code=404, detail="No migration plan found")
    plan["dry_run"] = False
    session["migration_plan"] = plan
    return {"status": "confirmed", "dry_run": False, "session_id": session_id}


@router.post("/v1/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request):
    """Cancel session, linked pipeline run, repo locks, and stale migration state."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)

    body = {}
    try:
        body = await request.json() or {}
    except Exception:
        pass

    from ado2gh.agents.migration_agent.session_lifecycle import cancel_agent_session

    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    result = await cancel_agent_session(
        session,
        action=str(body.get("action", "stop")),
        accel_post=_accel_post,
        session_token=session_token,
    )
    result["session_id"] = session_id
    return result
