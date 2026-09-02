"""Pipeline run and live approval route handlers."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    LiveApprovalCreateRequest,
    LiveApprovalDecisionRequest,
    LiveApprovalItem,
    LiveApprovalListResponse,
    PipelineRunResponse,
    PipelineRunStartApprovedRequest,
    PipelineRunStartRequest,
    PipelineStepDefinition,
)
from ado2gh.api.live_approval_store import register_migrate_executor, register_pipeline_executor
from ado2gh.api.pipeline_runner import (
    ACCELERATOR_PIPELINE_STEPS,
    MIGRATE_UI_PIPELINE_STEPS,
    PipelineRunStore,
    enrich_pipeline_run_dict,
    resolve_pipeline_step_defs,
)
from ado2gh.api.platform_rbac import (
    operator_requires_live_approval,
    require_approve_live_execution,
    require_operate,
)
from ado2gh.api.profile_governance import (
    ProfileGovernanceError,
    assert_profile_active_for_run,
)
from ado2gh.state.factory import create_state_db
from services.accelerator_api.routes._shared import (
    _execute_approved_migrate,
    _execute_approved_pipeline,
    _governance_http_error,
    _live_store,
    _platform_user,
    _runner,
    _settings,
)

router = APIRouter()

register_migrate_executor(_execute_approved_migrate)
register_pipeline_executor(_execute_approved_pipeline)


@router.get("/v1/pipeline/steps", response_model=list[PipelineStepDefinition])
def pipeline_steps(context: str = "migrate"):
    steps = MIGRATE_UI_PIPELINE_STEPS if context == "migrate" else ACCELERATOR_PIPELINE_STEPS
    return [PipelineStepDefinition(**s) for s in steps]


@router.get("/v1/pipeline/runs")
def list_pipeline_runs(limit: int = 20, offset: int = 0):
    runs, total = PipelineRunStore.list_runs(limit=limit, offset=offset)
    db = create_state_db(_settings.load().advanced.db_path)
    return {
        "runs": [
            enrich_pipeline_run_dict(r.to_dict(), db=db) for r in runs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": PipelineRunStore.summary(),
    }


@router.get("/v1/pipeline/runs/{run_id}")
def get_pipeline_run(run_id: str):
    run = PipelineRunStore.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db = create_state_db(_settings.load().advanced.db_path)
    return {"run": enrich_pipeline_run_dict(run.to_dict(), db=db)}


@router.post("/v1/pipeline/runs/{run_id}/cancel")
def cancel_pipeline_run(run_id: str):
    if not _runner.cancel(run_id):
        run = PipelineRunStore.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")
    run = PipelineRunStore.get(run_id)
    return {"run": run.to_dict() if run else None, "cancelled": True}


@router.post("/v1/pipeline/runs", response_model=PipelineRunResponse)
def start_pipeline_run(req: PipelineRunStartRequest, request: Request):
    active = _settings.get_active_profile()
    if not active:
        raise HTTPException(status_code=403, detail="profile_not_active")
    try:
        assert_profile_active_for_run(active)
    except ProfileGovernanceError as exc:
        raise _governance_http_error(exc)
    adv = _settings.load().advanced
    dry = req.dry_run if req.dry_run is not None else adv.dry_run_default
    _settings.apply_to_process_env()
    default_step_ids = [s["id"] for s in MIGRATE_UI_PIPELINE_STEPS]
    step_ids = req.steps or default_step_ids
    user = _platform_user(request)
    run = PipelineRunStore.create(
        req.name, dry, req.phase, req.wave_id,
        step_defs=resolve_pipeline_step_defs(step_ids),
        started_by_user_id=getattr(user, "id", None) if user else None,
        started_by_username=getattr(user, "username", None) if user else "admin",
        started_by_display_name=(
            getattr(user, "display_name", None) or getattr(user, "username", None)
        ) if user else "admin",
        repository_id=req.repository_id,
        migrate_deps_only=req.migrate_deps_only,
    )
    skip_live_gate = req.agent_live_approved and not dry
    if operator_requires_live_approval(user, dry) and not skip_live_gate:
        store = _live_store()
        store.create_or_get_pending(
            user,
            "pipeline_run",
            run.id,
            profile_id=active.id,
            reason_request=f"Pipeline live run: {req.name}",
            context={"run_id": run.id, "steps": step_ids},
        )
        run.live_approval_status = "pending"
        run.status = "awaiting_approval"
        run.updated_at = run.created_at
        if run.steps:
            run.steps[0].message = "Waiting for live execution approval"
        db = create_state_db(adv.db_path)
        return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))
    run.live_approval_status = "approved" if skip_live_gate else (
        "auto_approved" if not dry else "not_required"
    )
    _runner.start_async(run.id, step_ids)
    db = create_state_db(adv.db_path)
    return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))


@router.post("/v1/pipeline/runs/{run_id}/start", response_model=PipelineRunResponse)
def start_existing_pipeline_run(
    run_id: str,
    request: Request,
    req: PipelineRunStartApprovedRequest | None = None,
):
    """Start a pipeline run that was created but not started (e.g. awaiting approval)."""
    run = PipelineRunStore.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        db = create_state_db(_settings.load().advanced.db_path)
        return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))

    user = _platform_user(request)
    skip_live_gate = bool(req and req.agent_live_approved and not run.dry_run)
    if run.status == "awaiting_approval":
        if operator_requires_live_approval(user, run.dry_run) and not skip_live_gate:
            raise HTTPException(status_code=409, detail="awaiting_approval")
        run.live_approval_status = "approved" if skip_live_gate else "auto_approved"
    elif run.status not in ("pending", "awaiting_approval"):
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")

    step_ids = [s.id for s in run.steps]
    run.status = "pending"
    run.updated_at = run.created_at
    _runner.start_async(run_id, step_ids)
    db = create_state_db(_settings.load().advanced.db_path)
    return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))


@router.get("/v1/platform/approvals", response_model=LiveApprovalListResponse)
def list_live_approvals(request: Request, status: str = "pending"):
    require_approve_live_execution(request)
    store = _live_store()
    items = [LiveApprovalItem(**row) for row in store.list_approvals(status=status)]
    return LiveApprovalListResponse(approvals=items)


@router.get("/v1/platform/approvals/{approval_id}", response_model=LiveApprovalItem)
def get_live_approval(approval_id: str, request: Request):
    user = require_operate(request)
    store = _live_store()
    row = store.get_approval(approval_id, requester=user)
    return LiveApprovalItem(**row)


@router.post("/v1/platform/approvals", response_model=LiveApprovalItem)
def create_live_approval(req: LiveApprovalCreateRequest, request: Request):
    user = require_operate(request)
    store = _live_store()
    row = store.create_or_get_pending(
        user,
        req.scope_type,
        req.scope_id,
        profile_id=req.profile_id,
        reason_request=req.reason_request,
        context=req.context,
    )
    return LiveApprovalItem(**row)


@router.post("/v1/platform/approvals/{approval_id}/approve", response_model=LiveApprovalItem)
def approve_live_execution(
    approval_id: str, req: LiveApprovalDecisionRequest, request: Request,
):
    approver = require_approve_live_execution(request)
    store = _live_store()
    row = store.approve(approval_id, approver, req.reason)
    return LiveApprovalItem(**row)


@router.post("/v1/platform/approvals/{approval_id}/deny", response_model=LiveApprovalItem)
def deny_live_execution(
    approval_id: str, req: LiveApprovalDecisionRequest, request: Request,
):
    approver = require_approve_live_execution(request)
    store = _live_store()
    row = store.deny(approval_id, approver, req.reason)
    return LiveApprovalItem(**row)
