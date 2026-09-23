"""Routes for the forms the agent uses to prompt the operator: submit, stream the response, cancel."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ado2gh.agents.migration_agent.constants import (
    SSE_MAX_EVENTS_PER_STREAM,
    agent_runtime_settings,
)
from ado2gh.agents.migration_agent.hitl.forms import (
    migration_ready_reply,
)
from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
    live_execution_block_message,
)
from ado2gh.agents.migration_agent.runtime.orchestrator import (
    continue_session_graph,
    continue_session_graph_stream,
)
from ado2gh.agents.migration_agent.session.state import (
    OrchestratorResult,
    set_session_idle,
)
from ado2gh.api.migration_work_plan import work_items_summary
from services.agent.routes._helpers import (
    SSE_EVENT_LIMIT_REPLY,
    FormSubmitRequest,
    _accel_get,
    _accel_post,
    _add_message,
    _build_migration_plan,
    _get_accessible_session,
    _prune_stale_thinking_events,
    _require_operate,
    _session_accel_token,
    _session_payload,
    _session_token_from_request,
    _try_start_pev_run,
    client_error_detail,
)
from services.agent.routes.form_guard import reject_if_stale_form

router = APIRouter()


def _resolve_pending_form(session: dict[str, Any]) -> dict[str, Any] | None:
    """Return the active pending form, rebuilding from operator-input state if needed."""
    form = session.get("pending_form")
    if form:
        return form
    from ado2gh.agents.migration_agent.hitl.operator_input import (
        operator_input_to_form,
        pending_operator_input,
    )

    request = pending_operator_input(session)
    if request is None:
        return None
    form = operator_input_to_form(request, session)
    session["pending_form"] = form
    return form


async def _continue_graph_after_form(
    session: dict[str, Any],
    *,
    message: str,
    form_id: str,
    values: dict[str, Any],
    session_token: str | None,
) -> OrchestratorResult:
    """Resume an interrupted graph or continue with a synthetic user message.

    Args:
        session: Session dict for the thread.
        message: Text used when the thread is not paused at an interrupt.
        form_id: Id of the form the operator just answered.
        values: The operator's answers, passed through as the interrupt's
            resume payload alongside ``form_id``.
        session_token: Bearer token forwarded to the accelerator on behalf of
            the session, when the caller has one.

    Returns:
        The ``OrchestratorResult`` from resuming the interrupted graph (or
        starting a fresh turn with the synthetic message), same as
        ``continue_session_graph``.
    """
    return await continue_session_graph(
        session,
        message,
        resume_value={"form_id": form_id, "values": values},
        deps={
            "accel_get": _accel_get,
            "accel_post": _accel_post,
            "build_plan": _build_migration_plan,
            "session_token": session_token,
        },
    )


async def _ensure_repo_valid_for_migration(
    repo_id: str,
    session: dict[str, Any],
    session_token: str | None,
) -> str | None:
    """Load discovery if needed and validate the repository id."""
    from ado2gh.agents.migration_agent.utils import load_discovery_snapshot, validate_repo_against_discovery

    await load_discovery_snapshot(session, _accel_get, session_token=session_token)
    return validate_repo_against_discovery(repo_id, session.get("discovery_snapshot"))


@router.post("/v1/sessions/{session_id}/form-submit")
async def submit_session_form(
    session_id: str, req: FormSubmitRequest, request: Request,
) -> dict[str, Any]:
    """Submit the pending form and return the session with the agent response.

    Answers 409 when no form is pending.
    """
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    form = _resolve_pending_form(session)
    if not form:
        raise HTTPException(status_code=409, detail="no_pending_form")
    reject_if_stale_form(session_id, session, form, req)

    from ado2gh.agents.migration_agent.hitl.intake import format_form_submission_summary, prepare_form_submission

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
            raise HTTPException(
                status_code=502,
                detail=f"Pipeline inventory scan failed: {client_error_detail(exc)}",
            ) from exc
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session.pop("plan_review_presented", None)
        session["status"] = "idle"
        session["start_pev"] = False
        orch = await _continue_graph_after_form(
            session,
            message=outcome["message"],
            form_id=form_id,
            values=values,
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
        session["pending_clarification"] = None
        session["start_pev"] = False
        orch = await _continue_graph_after_form(
            session,
            message=outcome["message"],
            form_id=form_id,
            values=values,
            session_token=session_token,
        )
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = orch.reply or outcome["message"]
        if orch.pending_form:
            payload["pending_form"] = orch.pending_form
        return payload

    if outcome["status"] == "invoke_planner":
        repo_id = str(outcome.get("repository_id") or "").strip()
        if repo_id:
            session["plan_repository_id"] = repo_id
        session["dry_run"] = outcome.get("dry_run", session.get("dry_run", True))
        session["execution_mode_confirmed"] = True
        session["status"] = "idle"
        session["pending_clarification"] = None
        reply = outcome.get("message") or f"Proceeding to build the migration plan for '{repo_id}'."
        orch = await _continue_graph_after_form(
            session,
            message=reply,
            form_id=form_id,
            values=values,
            session_token=session_token,
        )
        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = _session_payload(session_id)
        payload["reply"] = orch.reply or reply
        if orch.pending_form:
            payload["pending_form"] = orch.pending_form
        return payload

    if outcome["status"] == "operator_input_continue":
        from ado2gh.agents.migration_agent.hitl.forms import _plan_confirmation_reply, sanitize_form
        from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form
        from ado2gh.agents.migration_agent.nodes.executor.plan import finalize_agent_migration_plan

        plan = session.get("migration_plan") or {}
        plan = finalize_agent_migration_plan(plan, session)
        session["migration_plan"] = plan
        form = sanitize_form(build_plan_review_form(session), session)
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

    if outcome["status"] == "form_incomplete":
        # hitl/intake.py returns this when a required field was left blank; the
        # form stays pending so the operator can answer it instead of the
        # submission silently continuing the graph with an incomplete form.
        session["pending_form"] = outcome.get("form") or form
        raise HTTPException(
            status_code=422,
            detail={"code": "form_incomplete", "missing": outcome.get("missing_fields", [])},
        )

    if outcome["status"] == "inventory_gaps":
        from ado2gh.api.migration_work_plan import apply_operator_secret_mappings, plan_narrative_from_work_items
        from ado2gh.models import ExecutionMode

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
        from ado2gh.agents.migration_agent.nodes.executor.plan import resolve_migration_phase

        plan_phase = resolve_migration_phase(session, plan)
        plan["narrative"] = plan_narrative_from_work_items(
            plan_phase or "",
            plan["work_items"],
            mode=ExecutionMode.from_dry_run(
                dry_run=bool(session.get("dry_run", True)),
            ),
            repository_id=plan.get("repository_id"),
        )
        session["migration_plan"] = plan
        from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
        from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form

        form = sanitize_form(build_plan_review_form(session), session)
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
        orch = await _continue_graph_after_form(
            session,
            message=outcome["message"],
            form_id=form_id,
            values=values,
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
    orch = await _continue_graph_after_form(
        session,
        message=outcome.get("message", ""),
        form_id=form_id,
        values=values,
        session_token=session_token,
    )
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _session_payload(session_id)
    payload["reply"] = orch.reply or "Form submitted."
    if orch.pending_form:
        payload["pending_form"] = orch.pending_form
    return payload


@router.post("/v1/sessions/{session_id}/form-submit-stream")
async def submit_session_form_stream(
    session_id: str, req: FormSubmitRequest, request: Request,
) -> StreamingResponse:
    """Stream agent events, as a server-sent event stream, after a form submission."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    form = _resolve_pending_form(session)
    if not form:
        raise HTTPException(status_code=409, detail="no_pending_form")
    reject_if_stale_form(session_id, session, form, req)

    from ado2gh.agents.migration_agent.hitl.intake import format_form_submission_summary, prepare_form_submission

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

        async def repo_error_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'kind': 'thinking', 'content': validation_error, 'role': 'system', 'subagent': 'orchestrator'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': 'message', 'content': validation_error, 'role': 'assistant'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': validation_error, 'pending_form': error_form}, default=str)}\n\n"

        return StreamingResponse(
            repo_error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    if outcome["status"] == "form_incomplete":
        # Same contract as the non-streaming route above: a required field left
        # blank answers with a single 422 response carrying a machine-readable
        # code, not a server-sent event frame — there is no partial run yet to
        # narrate.
        session["pending_form"] = outcome.get("form") or form
        raise HTTPException(
            status_code=422,
            detail={"code": "form_incomplete", "missing": outcome.get("missing_fields", [])},
        )

    synthetic_msg: str | None = None

    if outcome["status"] == "operator_remediate":
        if outcome.get("remediation") == "run_pipeline_inventory":
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
                raise HTTPException(
                status_code=502,
                detail=f"Pipeline inventory scan failed: {client_error_detail(exc)}",
            ) from exc
            session.pop("migration_plan", None)
            session["plan_approved"] = False
            session.pop("plan_review_presented", None)
        synthetic_msg = outcome.get("message") or outcome.get("reply", "Continue operator remediation.")
    elif outcome["status"] == "operator_input_continue":
        from ado2gh.agents.migration_agent.hitl.forms import _plan_confirmation_reply, sanitize_form
        from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form
        from ado2gh.agents.migration_agent.nodes.executor.plan import finalize_agent_migration_plan

        plan = session.get("migration_plan") or {}
        plan = finalize_agent_migration_plan(plan, session)
        session["migration_plan"] = plan
        review_form = sanitize_form(build_plan_review_form(session), session)
        session["pending_form"] = review_form
        reply = _plan_confirmation_reply(session)
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def operator_input_continue_stream() -> AsyncIterator[str]:
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
        synthetic_msg = outcome.get("message") or outcome.get("reply", "Revise the migration plan.")
    elif outcome["status"] == "invoke_planner":
        repo_id = str(outcome.get("repository_id") or "").strip()
        if repo_id:
            session["plan_repository_id"] = repo_id
        session["dry_run"] = outcome.get("dry_run", session.get("dry_run", True))
        session["execution_mode_confirmed"] = True
        synthetic_msg = outcome.get("message") or (
            f"Proceeding to build the migration plan for '{repo_id}'."
        )
        _add_message(session_id, "assistant", synthetic_msg, kind="message")
    elif outcome["status"] == "operator_input_unresolved":
        pending = outcome.get("form") or form
        session["pending_form"] = pending
        reply = outcome.get("reply", "Unresolved operator input")

        async def operator_input_unresolved_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': 'form_request', 'content': pending.get('title', ''), 'meta': pending}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply, 'pending_form': pending}, default=str)}\n\n"

        return StreamingResponse(
            operator_input_unresolved_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    elif outcome["status"] == "plan_unconfirmed":
        pending = outcome["form"]
        session["pending_form"] = pending
        reply = outcome["reply"]

        async def confirm_error_stream() -> AsyncIterator[str]:
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

        async def confirmed_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply}, default=str)}\n\n"

        return StreamingResponse(
            confirmed_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    if outcome["status"] == "plan_blocked":
        blocked = outcome["reply"]

        async def blocked_stream() -> AsyncIterator[str]:
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
        synthetic_msg = outcome.get("message") or outcome.get("reply", "Revise the migration plan.")
    elif outcome["status"] == "inventory_gaps":
        from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
        from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form
        from ado2gh.api.migration_work_plan import apply_operator_secret_mappings, plan_narrative_from_work_items
        from ado2gh.models import ExecutionMode

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
        from ado2gh.agents.migration_agent.nodes.executor.plan import resolve_migration_phase

        plan_phase = resolve_migration_phase(session, plan)
        plan["narrative"] = plan_narrative_from_work_items(
            plan_phase or "",
            plan["work_items"],
            mode=ExecutionMode.from_dry_run(
                dry_run=bool(session.get("dry_run", True)),
            ),
            repository_id=plan.get("repository_id"),
        )
        session["migration_plan"] = plan
        review_form = sanitize_form(build_plan_review_form(session), session)
        session["pending_form"] = review_form
        reply = (
            f"Saved {len(mappings)} service connection mapping(s). "
            "Review the migration plan and confirm when ready."
        )
        _add_message(session_id, "assistant", reply, kind="message")
        session["updated_at"] = datetime.now(timezone.utc).isoformat()

        async def inventory_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'kind': 'message', 'role': 'assistant', 'content': reply}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': 'form_request', 'content': review_form.get('title', ''), 'meta': review_form}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': reply, 'pending_form': review_form}, default=str)}\n\n"

        return StreamingResponse(
            inventory_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    elif synthetic_msg is None:
        synthetic_msg = outcome.get("message") or outcome.get("reply")
        if not synthetic_msg:
            raise HTTPException(
                status_code=400,
                detail=f"Unhandled form outcome status: {outcome.get('status')}",
            )

    session["status"] = "idle"
    session["pending_clarification"] = None
    session["start_pev"] = False
    heartbeat_interval_seconds = agent_runtime_settings().sse_heartbeat_interval_seconds

    async def event_stream() -> AsyncIterator[str]:
        last_heartbeat = asyncio.get_event_loop().time()
        events_sent = 0
        try:
            # aclosing: see message_routes — returning at the cap from inside the
            # `async for` must still close the graph stream behind it.
            async with aclosing(
                continue_session_graph_stream(
                    session,
                    synthetic_msg,
                    resume_value={"form_id": form_id, "values": values},
                    deps={
                        "accel_get": _accel_get,
                        "accel_post": _accel_post,
                        "build_plan": _build_migration_plan,
                        "session_token": session_token,
                    },
                ),
            ) as events:
                async for event in events:
                    events_sent += 1
                    if events_sent > SSE_MAX_EVENTS_PER_STREAM:
                        session["status"] = "idle"
                        yield f"data: {json.dumps({'kind': '__done__', 'reply': SSE_EVENT_LIMIT_REPLY}, default=str)}\n\n"
                        return
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
                    if now - last_heartbeat >= heartbeat_interval_seconds:
                        yield f"data: {json.dumps({'kind': 'heartbeat', 'content': ''}, default=str)}\n\n"
                        last_heartbeat = now
        except Exception as exc:
            session["status"] = "idle"
            detail = client_error_detail(exc)
            yield f"data: {json.dumps({'kind': 'thinking', 'content': f'Error: {detail}', 'role': 'system', 'subagent': 'orchestrator'}, default=str)}\n\n"
            yield f"data: {json.dumps({'kind': '__done__', 'reply': detail}, default=str)}\n\n"
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


@router.post("/v1/sessions/{session_id}/form-cancel")
def cancel_session_form(session_id: str, request: Request) -> dict[str, Any]:
    """Cancel the pending form, clear the migration plan, and return to idle."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    from ado2gh.agents.migration_agent.session.lifecycle import persist_session_snapshot
    from ado2gh.agents.migration_agent.session.state import reset_session_for_new_migration

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
