"""Pipeline run endpoints under `/v1/pipeline`.

Covers the step catalogue, the run list and detail views, cancellation, and the
two ways a run is started. The platform-wide live-execution approval queue those
starts park in lives in `approval_routes.py` and is mounted onto this router at
the bottom of this module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    LiveApprovalCreateRequest,
    PipelineRunResponse,
    PipelineRunStartRequest,
    PipelineStepDefinition,
)
from ado2gh.api.live_approval_store import (
    PIPELINE_RUN_CONTEXT_RUN_ID,
    PIPELINE_RUN_SCOPE_TYPE,
    pipeline_run_scope_id,
)
from ado2gh.api.pipeline_runner import (
    ACCELERATOR_PIPELINE_STEPS,
    MIGRATE_UI_PIPELINE_STEPS,
    PipelineRunStore,
    enrich_pipeline_run_dict,
    resolve_pipeline_step_defs,
)
from ado2gh.api.platform_rbac import (
    operator_requires_live_approval,
    require_operate,
)
from ado2gh.api.profile_governance import (
    ProfileGovernanceError,
    assert_profile_active_for_run,
)
from ado2gh.models import ExecutionMode
from ado2gh.state.factory import create_state_db
from services.accelerator_api.routes._shared import (
    _governance_http_error,
    _live_store,
    _platform_user,
    _runner,
    _settings,
)
from services.accelerator_api.routes.approval_routes import router as _approval_router

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ado2gh.api.pipeline_models import PipelineRun
    from ado2gh.auth.models import PlatformUser

router = APIRouter()


def _park_for_approval(
    run: PipelineRun, user: PlatformUser | None,
) -> None:
    """Queue a live run for an approver's decision and park it until one arrives.

    The one place a pipeline run enters the approval queue, so the create route
    and the start route cannot drift on what parking means (GAP-066). The queue
    entry is itself the record that a live run was asked for (CA-004), and the
    parked status is one no runner picks up.

    The context carries only the run id, built through ``pipeline_run_scope_id``
    so the scope shown to the approver and the context checked against it are
    the same statement (GAP-108). Step selection is not part of the context —
    the run already persists which steps it executes.

    Args:
        run: The live run to park. Mutated in place — the registry hands out the
            live object, so the caller's copy is the parked one.
        user: The signed-in caller asking to execute live, read from the session
            rather than from the request body.
    """
    active = _settings.get_active_profile()
    _live_store().create_or_get_pending(
        user,
        LiveApprovalCreateRequest(
            scope_type=PIPELINE_RUN_SCOPE_TYPE,
            scope_id=pipeline_run_scope_id(run.id),
            profile_id=active.id if active else None,
            reason_request=f"Pipeline live run: {run.name}",
            context={PIPELINE_RUN_CONTEXT_RUN_ID: run.id},
        ),
    )
    run.live_approval_status = "pending"
    run.status = "awaiting_approval"
    run.updated_at = run.created_at
    if run.steps:
        run.steps[0].message = "Waiting for live execution approval"


@router.get("/v1/pipeline/steps", response_model=list[PipelineStepDefinition])
def pipeline_steps(context: str = "migrate") -> list[PipelineStepDefinition]:
    """List the step definitions a pipeline run executes, in execution order.

    Args:
        context: Which step catalogue to return. ``migrate`` (the default) gives
            the steps the migration console drives; any other value gives the
            accelerator's own step list.

    Returns:
        The step definitions for that context, in the order they run.
    """
    steps = MIGRATE_UI_PIPELINE_STEPS if context == "migrate" else ACCELERATOR_PIPELINE_STEPS
    return [PipelineStepDefinition(**s) for s in steps]


@router.get("/v1/pipeline/runs")
def list_pipeline_runs(limit: int = 20, offset: int = 0) -> dict[str, object]:
    """List pipeline runs, newest first, together with the dashboard counts.

    Args:
        limit: Maximum number of runs on the page.
        offset: Number of runs to skip from the newest end.

    Returns:
        ``runs``: the requested page, each run enriched with display-friendly
        started-by and live-approval labels; ``total``: how many runs exist in
        all; ``limit`` and ``offset``: the paging values echoed back; and
        ``summary``: run counts by outcome for the dashboard tiles.
    """
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
def get_pipeline_run(run_id: str) -> dict[str, object]:
    """Fetch a single pipeline run and its per-step progress.

    Args:
        run_id: Identifier of the run to read.

    Returns:
        ``run``: the run, enriched with display-friendly started-by and
        live-approval labels.

    Raises:
        HTTPException: 404 when no run carries that identifier.
    """
    run = PipelineRunStore.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db = create_state_db(_settings.load().advanced.db_path)
    return {"run": enrich_pipeline_run_dict(run.to_dict(), db=db)}


@router.post("/v1/pipeline/runs/{run_id}/cancel")
def cancel_pipeline_run(run_id: str) -> dict[str, object]:
    """Cancel an in-flight pipeline run.

    The run stops at its next step boundary; the step already executing is
    allowed to finish.

    Args:
        run_id: Identifier of the run to cancel.

    Returns:
        ``run``: the run as it stood when cancellation was requested, or null if
        it had disappeared from the registry; ``cancelled``: always true once
        the request was accepted.

    Raises:
        HTTPException: 404 when no run carries that identifier, 409 when the run
            has already reached a status that cannot be cancelled.
    """
    if not _runner.cancel(run_id):
        run = PipelineRunStore.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")
    run = PipelineRunStore.get(run_id)
    return {"run": run.to_dict() if run else None, "cancelled": True}


@router.post("/v1/pipeline/runs", response_model=PipelineRunResponse)
def start_pipeline_run(req: PipelineRunStartRequest, request: Request) -> PipelineRunResponse:
    """Create a pipeline run and start it, or park it awaiting live approval.

    Runs are dry by default: when the body leaves ``dry_run`` unset the
    deployment-wide advanced setting decides. A caller whose role does not carry
    live-execution authority gets the run parked at ``awaiting_approval`` with an
    approval request queued for an approver, instead of started.

    Args:
        req: Run name, optional step selection, phase, wave, repository and
            dry-run choice.
        request: Incoming request, used to identify the signed-in caller.

    Returns:
        ``run``: the created run, either already running or parked awaiting
        approval, with display-friendly started-by and live-approval labels.

    Raises:
        HTTPException: 403 when no migration profile is active, or the
            governance status (403/404/409) when the active profile is not
            allowed to run migrations.
    """
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
    # Live authority comes only from server-side state — the authenticated user's role
    # here, or an approved LiveApprovalStore row on the /start route. The request body
    # cannot influence it: PipelineRunStartRequest declares no live-approval field and
    # forbids extras, so the old self-certifying agent_live_approved is a 422 (GAP-004).
    # Asked before the run is persisted: a refusal must leave nothing behind for
    # /start to pick up (GAP-066).
    needs_approval = operator_requires_live_approval(
        user, ExecutionMode.from_dry_run(dry_run=dry),
    )
    run = PipelineRunStore.create(
        req.name,
        dry_run=dry,
        phase=req.phase,
        wave_id=req.wave_id,
        step_defs=resolve_pipeline_step_defs(step_ids),
        started_by_user_id=getattr(user, "id", None) if user else None,
        started_by_username=getattr(user, "username", None) if user else "admin",
        started_by_display_name=(
            getattr(user, "display_name", None) or getattr(user, "username", None)
        ) if user else "admin",
        repository_id=req.repository_id,
        migrate_deps_only=req.migrate_deps_only,
        override_reason=req.override_reason,
    )
    if needs_approval:
        _park_for_approval(run, user)
        db = create_state_db(adv.db_path)
        return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))
    run.live_approval_status = "auto_approved" if not dry else "not_required"
    _runner.start_async(run.id, step_ids)
    db = create_state_db(adv.db_path)
    return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))


@router.post("/v1/pipeline/runs/{run_id}/start", response_model=PipelineRunResponse)
def start_existing_pipeline_run(run_id: str, request: Request) -> PipelineRunResponse:
    """Start a pipeline run that was created but not started (for example awaiting approval).

    The route takes no request body. Whether a parked run may go live is decided
    entirely from server-side state, so there is nothing for a caller to send.

    Calling it on a run that is already running is a no-op that returns the run
    as it stands.

    Args:
        run_id: Identifier of the run to start.
        request: Incoming request, used to identify the signed-in caller.

    Returns:
        ``run``: the run after the start attempt, with display-friendly
        started-by and live-approval labels.

    Raises:
        HTTPException: 401 or 403 when the caller may not operate or may not
            execute live, 404 when no run carries that identifier, 409 when a
            live run still awaits approval or when the run has already reached a
            status that cannot be started.
    """
    run = PipelineRunStore.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        db = create_state_db(_settings.load().advanced.db_path)
        return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))

    user = require_operate(request)
    if run.status not in ("pending", "awaiting_approval"):
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")

    # A live run is released either by a caller who can approve live execution, or by
    # LiveApprovalStore itself once an approver decides — which calls the registered
    # executor directly and never comes back through this route. Since the handler
    # reads no body, a caller posting its own approval claim (the old
    # agent_live_approved, GAP-004) changes nothing here. `pending` is gated the same
    # way as `awaiting_approval`: a live run that was never parked has not been
    # approved either, whoever created it (GAP-066).
    step_ids = [s.id for s in run.steps]
    approved = _live_store().has_approved(PIPELINE_RUN_SCOPE_TYPE, run_id)
    if not approved:
        if operator_requires_live_approval(
            user, ExecutionMode.from_dry_run(dry_run=run.dry_run),
        ):
            _park_for_approval(run, user)
            raise HTTPException(status_code=409, detail="awaiting_approval")
        run.live_approval_status = "auto_approved" if not run.dry_run else "not_required"

    run.status = "pending"
    run.updated_at = run.created_at
    _runner.start_async(run_id, step_ids)
    db = create_state_db(_settings.load().advanced.db_path)
    return PipelineRunResponse(run=enrich_pipeline_run_dict(run.to_dict(), db=db))


# Mounted last so this module's own routes keep the registration order they were
# written and tested in, and main.py keeps one include_router call for both.
router.include_router(_approval_router)
