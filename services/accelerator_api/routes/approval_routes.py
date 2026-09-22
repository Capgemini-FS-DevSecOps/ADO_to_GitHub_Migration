"""Live-execution approval endpoints under `/v1/platform/approvals`.

The approval queue is platform-wide: a live pipeline run and a live
`/v1/migrate/*` call both park here when the caller may operate but may not
self-approve. This module also registers the two callbacks the store invokes
once an approval is granted, so the queue and the work it releases stay
described in one place. Mounted as a sub-router of `pipeline_routes` so
`main.py` keeps a single `include_router` call for both.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    LiveApprovalCreateRequest,
    LiveApprovalDecisionRequest,
    LiveApprovalItem,
    LiveApprovalListResponse,
)
from ado2gh.api.live_approval_store import register_migrate_executor, register_pipeline_executor
from ado2gh.api.platform_rbac import require_approve_live_execution, require_operate
from services.accelerator_api.routes._shared import (
    _execute_approved_migrate,
    _execute_approved_pipeline,
    _live_store,
)

router = APIRouter()

register_migrate_executor(_execute_approved_migrate)
register_pipeline_executor(_execute_approved_pipeline)


@router.get("/v1/platform/approvals", response_model=LiveApprovalListResponse)
def list_live_approvals(request: Request, status: str = "pending") -> LiveApprovalListResponse:
    """List the queued live-execution approvals in one status.

    Args:
        request: Incoming request, used to check the caller may decide live runs.
        status: Approval status to list, one of ``pending``, ``approved`` or
            ``denied``.

    Returns:
        ``approvals``: the matching approval requests.

    Raises:
        HTTPException: 403 when the caller may not approve live execution.
    """
    require_approve_live_execution(request)
    store = _live_store()
    items = [LiveApprovalItem(**row) for row in store.list_approvals(status=status)]
    return LiveApprovalListResponse(approvals=items)


@router.get("/v1/platform/approvals/{approval_id}", response_model=LiveApprovalItem)
def get_live_approval(approval_id: str, request: Request) -> LiveApprovalItem:
    """Fetch one live-execution approval request and its decision, if any.

    A caller who is neither an admin nor an approver may only read an approval
    they opened themselves.

    Args:
        approval_id: Identifier of the approval to read.
        request: Incoming request, used to identify the signed-in caller.

    Returns:
        The approval request, including its status, reason and decision fields.

    Raises:
        HTTPException: 403 when the caller may not operate or may not see this
            approval, 404 when no such approval exists.
    """
    user = require_operate(request)
    store = _live_store()
    row = store.get_approval(approval_id, requester=user)
    return LiveApprovalItem(**row)


@router.post("/v1/platform/approvals", response_model=LiveApprovalItem)
def create_live_approval(req: LiveApprovalCreateRequest, request: Request) -> LiveApprovalItem:
    """Request approval to run a scope live, or return the pending request.

    Idempotent per scope: a second request for a scope already awaiting a
    decision returns the existing approval rather than queueing a duplicate.

    Args:
        req: Scope kind and id, migration profile and the reason live execution
            is being requested.
        request: Incoming request, used to identify the requesting caller.

    Returns:
        The pending approval request for that scope.

    Raises:
        HTTPException: 401 when the caller carries no identity — there is no
            one to attribute the approval request to, and ``require_operate``
            is deliberately permissive about a missing identity while
            authentication is disabled (GAP-110). 403 when the caller may not
            operate.
    """
    user = require_operate(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    store = _live_store()
    row = store.create_or_get_pending(user, req)
    return LiveApprovalItem(**row)


@router.post("/v1/platform/approvals/{approval_id}/approve", response_model=LiveApprovalItem)
def approve_live_execution(
    approval_id: str, req: LiveApprovalDecisionRequest, request: Request,
) -> LiveApprovalItem:
    """Approve a queued live run and release it for execution.

    Records the decision against the approver and hands the scope to its
    registered executor, so the parked work starts without a further call.

    Args:
        approval_id: Identifier of the approval to decide.
        req: The reason recorded alongside the decision.
        request: Incoming request, used to identify the approver.

    Returns:
        The approval, now carrying the approved status, approver and reason.

    Raises:
        HTTPException: 403 when the caller may not approve live execution, 404
            when no such approval exists, 409 when it was already decided.
    """
    approver = require_approve_live_execution(request)
    store = _live_store()
    row = store.approve(approval_id, approver, req.reason)
    return LiveApprovalItem(**row)


@router.post("/v1/platform/approvals/{approval_id}/deny", response_model=LiveApprovalItem)
def deny_live_execution(
    approval_id: str, req: LiveApprovalDecisionRequest, request: Request,
) -> LiveApprovalItem:
    """Deny a queued live run so that it never executes.

    Records the decision against the approver and tells the parked scope it was
    refused, leaving the work stopped.

    Args:
        approval_id: Identifier of the approval to decide.
        req: The reason recorded alongside the decision.
        request: Incoming request, used to identify the approver.

    Returns:
        The approval, now carrying the denied status, approver and reason.

    Raises:
        HTTPException: 403 when the caller may not approve live execution, 404
            when no such approval exists, 409 when it was already decided.
    """
    approver = require_approve_live_execution(request)
    store = _live_store()
    row = store.deny(approval_id, approver, req.reason)
    return LiveApprovalItem(**row)
