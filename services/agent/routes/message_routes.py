"""Chat message routes for the agent service, plain and SSE-streamed."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
)
from ado2gh.agents.migration_agent.route_helpers import (
    SessionMessageRequest,
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
)
from ado2gh.agents.migration_agent.runtime.orchestrator import (
    stream_user_message,
)
from ado2gh.agents.migration_agent.session.state import (
    is_session_busy,
)

router = APIRouter()


def _reject_if_session_busy(session: dict[str, Any]) -> None:
    if is_session_busy(session.get("status")):
        raise HTTPException(
            status_code=409,
            detail="Agent is busy — wait for the current step to finish or cancel it",
        )


@router.post("/v1/sessions/{session_id}/message")
async def session_message(
    session_id: str, req: SessionMessageRequest, request: Request,
) -> dict[str, Any]:
    """Send a user message to the agent and return the session with its reply.

    Answers 409 when the agent is still working or a form is waiting to be
    submitted.
    """
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    _reject_if_session_busy(session)
    if session.get("pending_form"):
        raise HTTPException(
            status_code=409,
            detail="pending_form — submit the form or cancel before sending a new message",
        )

    from ado2gh.agents.migration_agent.runtime.orchestrator import process_user_message

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
async def session_message_stream(
    session_id: str, req: SessionMessageRequest, request: Request,
) -> StreamingResponse:
    """Stream agent events (thinking, status, tool updates) via SSE using LangGraph."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    _reject_if_session_busy(session)
    if session.get("pending_form"):
        raise HTTPException(
            status_code=409,
            detail="pending_form — submit the form or cancel before sending a new message",
        )

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

    async def event_stream() -> AsyncIterator[str]:
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
