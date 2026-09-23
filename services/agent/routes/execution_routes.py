"""Execution and live-approval routes for agent migration sessions.

Covers starting a plan-execute-validate loop (PEV) run, the live-execution approval handshake (request,
approve, confirm, and the accelerator's internal resume/deny callbacks),
switching a session between dry-run and live, and cancelling a run."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from ado2gh.agents.migration_agent.policies import (
    attach_actor_to_session,
    can_execute_live_without_approval,
    enforce_live_mode_request,
)
from ado2gh.agents.migration_agent.session.state import (
    is_session_busy,
    set_session_idle,
)
from ado2gh.models import ExecutionMode
from services.agent.routes._helpers import (
    ApprovalRequest,
    ExecutionModeRequest,
    RunStatus,
    _accel_post,
    _add_message,
    _audit,
    _enqueue_session_live_approval,
    _get_accessible_session,
    _remember_run,
    _require_approve_live,
    _require_operate,
    _runs,
    _session_accel_token,
    _session_payload,
    _session_token_from_request,
    _sessions,
    _try_start_pev_run,
)

router = APIRouter()

# Returned when the platform approval store cannot be reached at all (a
# connection or timeout failure, as opposed to a response carrying its own
# status code, which is forwarded as-is).
_PLATFORM_UNREACHABLE_STATUS = 502


@router.post("/v1/sessions/{session_id}/run-pev")
async def run_pev(session_id: str, request: Request) -> dict[str, Any]:
    """Start planner → executor → validator against the accelerator (dry-run by default).

    Args:
        session_id: Session whose plan is executed.
        request: Used for the operate-permission check and to resolve the
            session's actor.

    Returns:
        A status payload with ``session_id``, ``status``, ``subagent``,
        ``dry_run`` and ``run_id``. When a run was already in progress and
        this call did not start a new one, the payload also carries
        ``blocked: True`` and ``execution_policy`` instead of starting a
        second run.

    Raises:
        HTTPException: 409 when the session has no migration plan yet, or
            the plan is blocked.
    """
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
async def request_live(session_id: str, request: Request) -> dict[str, Any]:
    """Queue a live-execution approval for this session and tell the operator."""
    session = _get_accessible_session(session_id, request)
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    await _enqueue_session_live_approval(session, session_token=session_token)
    _add_message(session_id, "system", "Live execution requested — awaiting approval")
    return _session_payload(session_id)


@router.post("/v1/sessions/{session_id}/approve")
async def approve_session(
    session_id: str, req: ApprovalRequest, request: Request,
) -> dict[str, Any]:
    """Approve or deny live execution for a session.

    Approving forwards to the platform approval queue when the session already
    has a pending approval id, and otherwise switches the session to live
    directly. Denying returns the session to idle and records the reason.
    """
    _require_approve_live(request)
    session = _get_accessible_session(session_id, request)
    if not req.approved:
        set_session_idle(session)
        session["approval"] = {"approved": False, "reason": req.reason}
        session["live_approval_status"] = "denied"
        approval_id = session.get("live_approval_id")
        # Audited before the platform forward is attempted, not after: the local
        # denial has to survive even when the call below raises, so a session
        # that says no is on durable record regardless of whether the platform
        # row could be reached (GAP-124 follow-up — see resume-live below for
        # the other half, refusing a resume once this record exists).
        _audit.record(
            "session.approve.denied",
            profile_id=session["profile_id"],
            session_id=session_id,
            metadata={"reason": req.reason, "outcome": "denied", "approval_id": approval_id},
        )
        # Close the matching platform approval row through the same deny path
        # the internal deny-live route uses, not just the local session state:
        # a denial that only sets local state leaves the platform row pending,
        # so a separately-privileged approver could later approve that stale
        # row. The local denial above already stands on its own record, and
        # resume-live now refuses a session with a final local denial, so a
        # forwarding failure here is reported and retriable rather than a
        # silent bypass.
        if approval_id:
            session_token = _session_token_from_request(request) or _session_accel_token(session_id)
            # The platform decision contract requires a non-empty reason
            # (`LiveApprovalDecisionRequest.reason`, min length one); the local
            # approval request does not, so an unfilled local reason still
            # closes the platform row with a stand-in reason rather than
            # failing the whole denial.
            platform_reason = req.reason or "Denied via agent session"
            try:
                await _accel_post(
                    f"/v1/platform/approvals/{approval_id}/deny",
                    {"reason": platform_reason},
                    session_token=session_token,
                )
            except httpx.HTTPStatusError as exc:
                _audit.record(
                    "session.approve.denied.forward_failed",
                    profile_id=session["profile_id"],
                    session_id=session_id,
                    metadata={
                        "approval_id": approval_id,
                        "error": "http_status",
                        "status_code": exc.response.status_code,
                    },
                )
                raise HTTPException(
                    status_code=exc.response.status_code,
                    detail="platform_denial_failed",
                ) from exc
            except httpx.HTTPError as exc:
                # Anything other than a response with a status code: a
                # connection or timeout failure reaching the platform. The
                # local denial is already durably audited above, so this is
                # reported rather than silently dropped.
                _audit.record(
                    "session.approve.denied.forward_failed",
                    profile_id=session["profile_id"],
                    session_id=session_id,
                    metadata={
                        "approval_id": approval_id,
                        "error": type(exc).__name__,
                    },
                )
                raise HTTPException(
                    status_code=_PLATFORM_UNREACHABLE_STATUS,
                    detail="platform_denial_failed",
                ) from exc
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

    # No approval row to forward to, so this branch *is* the live transition.
    # `_require_approve_live` above is a no-op whenever ADO2GH_AUTH_ENABLED is
    # unset — the shipped default — which used to leave the dry-run flag written
    # with nothing guarding it. Every entry point that turns a session from a
    # preview run into a real one must make that decision through the one
    # shared policy check, never by writing the flag directly (register items
    # THR-06-004, GAP-002 residual). Route the decision through that check
    # before the flag is written.
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    enforce_live_mode_request(request, ExecutionMode.LIVE, session=session)
    session["dry_run"] = False
    session["approval"] = {"approved": True, "reason": req.reason}
    _audit.record(
        "session.approve",
        profile_id=session["profile_id"],
        session_id=session_id,
        metadata={"reason": req.reason},
    )
    return {"session_id": session_id, "status": "executing"}


@router.post("/v1/sessions/{session_id}/confirm-live")
def confirm_live_execution(session_id: str, request: Request) -> dict[str, Any]:
    """Confirm real, live execution of the session's migration plan.

    A preview run is the default and a real run needs this explicit
    confirmation step (register item CA-001). Confirming is the *last* gate —
    nothing downstream re-checks the plan's dry-run flag — so the caller must
    already hold live authority here, unlike ``execution-mode`` which hands an
    unauthorised caller to the approval queue.
    """
    _require_operate(request)
    session = _get_accessible_session(session_id, request)
    plan = session.get("migration_plan")
    if not plan:
        raise HTTPException(status_code=404, detail="No migration plan found")
    attach_actor_to_session(session, getattr(request.state, "platform_user", None))
    enforce_live_mode_request(request, ExecutionMode.LIVE, session=session)
    plan["dry_run"] = False
    session["migration_plan"] = plan
    _audit.record(
        "session.confirm_live",
        profile_id=session.get("profile_id"),
        session_id=session_id,
        metadata={"plan_id": plan.get("plan_id"), "repos": plan.get("repo_order", [])},
    )
    return {"status": "confirmed", "dry_run": False, "session_id": session_id}


@router.patch("/v1/sessions/{session_id}/execution-mode")
async def update_execution_mode(
    session_id: str, req: ExecutionModeRequest, request: Request,
) -> dict[str, Any]:
    """Set dry-run versus live on an agent session (admins/approvers use live without approval queue)."""
    _require_operate(request)
    enforce_live_mode_request(request, ExecutionMode.from_dry_run(dry_run=req.dry_run))
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


@router.post("/v1/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
    """Cancel session, linked pipeline run, repository locks, and stale migration state."""
    _require_operate(request)
    session = _get_accessible_session(session_id, request)

    body = {}
    try:
        body = await request.json() or {}
    except Exception:
        pass

    from ado2gh.agents.migration_agent.session.lifecycle import cancel_agent_session

    session_token = _session_token_from_request(request) or _session_accel_token(session_id)
    result = await cancel_agent_session(
        session,
        action=str(body.get("action", "stop")),
        accel_post=_accel_post,
        session_token=session_token,
    )
    result["session_id"] = session_id
    return result


@router.post("/v1/internal/sessions/{session_id}/resume-live")
async def resume_live_internal(session_id: str) -> dict[str, str]:
    """Resume a session in live mode after the accelerator approved it.

    Service-to-service only — the caller must present the internal token header.
    Ensures a run record exists (reusing one already in progress, otherwise
    creating a new ``PLANNING`` run) before flipping the session to live.

    Args:
        session_id: Session the accelerator just approved for live execution.

    Returns:
        ``{"session_id": session_id, "status": "executing"}``.

    Raises:
        HTTPException: 404 when the session does not exist, 409 when the
            session carries a final local denial (``approve_session`` already
            recorded one, durably, before this call could arrive) — a stale
            platform approval row must not override that record.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("live_approval_status") == "denied":
        raise HTTPException(status_code=409, detail="session_locally_denied")
    session["dry_run"] = False
    session["live_approval_status"] = "approved"
    run_id = session.get("run_id")
    if not run_id or run_id not in _runs:
        run_id = str(uuid.uuid4())
        _remember_run(
            run_id,
            {
                "run_id": run_id,
                "session_id": session_id,
                "status": RunStatus.PLANNING,
                "request": {},
                "steps": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        session["run_id"] = run_id
    _audit.record(
        "session.resume_live",
        profile_id=session.get("profile_id"),
        session_id=session_id,
        metadata={
            "run_id": run_id,
            "live_approval_status": "approved",
            "approval_id": session.get("live_approval_id"),
            "source": "internal_service_to_service",
        },
    )
    return {"session_id": session_id, "status": "executing"}


@router.post("/v1/internal/sessions/{session_id}/deny-live")
def deny_live_internal(session_id: str, body: dict | None = None) -> dict[str, Any]:
    """Return a session to dry-run after the accelerator denied live execution.

    Service-to-service only — the caller must present the internal token header.

    Args:
        session_id: The session whose live request was denied.
        body: Optional JSON body carrying a ``reason`` shown to the operator.
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    reason = (body or {}).get("reason", "denied")
    set_session_idle(session)
    session["live_approval_status"] = "denied"
    session["approval"] = {"approved": False, "reason": reason}
    _add_message(session_id, "system", f"Live execution denied: {reason}")
    return {"session_id": session_id, "status": session["status"]}
