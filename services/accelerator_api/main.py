"""FastAPI REST service wrapping the Accelerator SDK."""
from __future__ import annotations

import os

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import *
from ado2gh.api.credentials.credential_validation import validate_ado_pat, validate_github_token
from ado2gh.api.migration_scan import (
    load_scan_results,
    persist_scan_results,
    scan_with_credentials,
)
from ado2gh.api.pipeline_runner import (
    ACCELERATOR_PIPELINE_STEPS,
    MIGRATE_UI_PIPELINE_STEPS,
    PipelineRunStore,
    PipelineRunner,
    enrich_pipeline_run_dict,
    resolve_pipeline_step_defs,
)
from ado2gh.api.settings_store import SettingsStore
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.redis_queue import RedisJobQueue
from ado2gh.state.job_store import JobStoreFactory
from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
from ado2gh.state.factory import create_state_db
from ado2gh.api.profile_governance import (
    assert_operator_can_submit,
    assert_profile_active_for_run,
    onboarding_status_payload,
    ProfileGovernanceError,
    write_profile_audit,
)
from ado2gh.auth.models import PlatformRole
from ado2gh.api.agentic_routes import enforce_live_gate, router as agentic_router
from ado2gh.api.routers.discovery_router import router as discovery_router
from ado2gh.api.routers.migration_router import router as migration_router
from ado2gh.api.live_approval_store import (
    LiveApprovalStore,
    migrate_scope_id,
    register_migrate_executor,
    register_pipeline_executor,
)
from ado2gh.api.platform_rbac import (
    operator_requires_live_approval,
    require_approve_live_execution,
    require_manage_models,
    require_manage_settings,
    require_operate,
)

try:
    from services.accelerator_api.auth_routes import router as auth_router, SESSION_COOKIE
except ImportError:
    from auth_routes import router as auth_router, SESSION_COOKIE
from ado2gh.auth.service import AuthService, auth_enabled

from services.accelerator_api.routes._shared import (
    _accel,
    _config_path,
    _platform_user,
    _require_admin,
    _live_store,
    _settings,
    _runner,
    _governance_http_error,
    _execute_approved_migrate,
    _execute_approved_pipeline,
)
from services.accelerator_api.routes.settings_routes import router as settings_router
from services.accelerator_api.routes.profile_routes import router as profile_router
from services.accelerator_api.routes.pipeline_routes import router as pipeline_router
from services.accelerator_api.routes.migrate_routes import router as migrate_features_router
from services.accelerator_api.routes.proxy_routes import router as proxy_router

app = FastAPI(title="ADO2GH Accelerator API", version="5.1.0")
app.include_router(agentic_router)
app.include_router(auth_router)
app.include_router(discovery_router)
app.include_router(migration_router)
app.include_router(settings_router)
app.include_router(profile_router)
app.include_router(pipeline_router)
app.include_router(migrate_features_router)
app.include_router(proxy_router)

_AUTH_EXEMPT = {
    "/health",
    "/ready",
    "/v1/auth/bootstrap-status",
    "/v1/auth/bootstrap",
    "/v1/auth/login",
    "/v1/auth/register",
}


@app.middleware("http")
async def platform_auth_middleware(request, call_next):
    if not auth_enabled():
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/v1/") or path in _AUTH_EXEMPT:
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
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _sync_platform_supplied_model() -> None:
    from ado2gh.api.llm.platform_managed_model import sync_on_startup

    sync_on_startup()


def _onboarding_redirect() -> str | None:
    from ado2gh.api.profile_governance import needs_profile_setup

    profiles = _settings.load().migration_profiles
    if needs_profile_setup(profiles):
        return "/onboarding/profile"
    return None


@app.get("/v1/onboarding/status", response_model=OnboardingStatusResponse)
def onboarding_status(request: Request):
    user = _platform_user(request)
    role = user.role if user else PlatformRole.OPERATOR
    profiles = _settings.load().migration_profiles
    return OnboardingStatusResponse(**onboarding_status_payload(profiles, role))


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse()


@app.get("/ready")
def ready():
    import shutil
    import subprocess

    from ado2gh.state.storage_config import StorageConfig
    from ado2gh.models import DEFAULT_MIGRATION_STRATEGY

    cfg = StorageConfig.from_env()
    checks = {"api": True, "storage_backend": cfg.backend.value}

    adv = SettingsStore().load().advanced
    strategy = adv.migration_strategy or DEFAULT_MIGRATION_STRATEGY
    try:
        global_cfg, _ = ConfigLoader.load(_config_path())
        strategy = global_cfg.get("migration_strategy", strategy)
    except Exception:
        pass
    checks["migration_strategy"] = strategy

    gh_path = shutil.which("gh")
    checks["gh"] = bool(gh_path)
    if not gh_path:
        checks["gh_hint"] = "Install GitHub CLI (gh) and gh-gei extension in the container image"

    gei_ok = False
    ado2gh_ok = False
    if gh_path:
        try:
            gh_cmd = [gh_path, "extension", "list"]
            result = subprocess.run(
                gh_cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                ext = result.stdout.lower()
                gei_ok = "gei" in ext
                ado2gh_ok = "ado2gh" in ext
        except Exception:
            gei_ok = False
            ado2gh_ok = False
    checks["gh_gei"] = gei_ok
    checks["gh_ado2gh"] = ado2gh_ok
    if gh_path and not ado2gh_ok:
        checks["gh_ado2gh_hint"] = "Run: gh extension install github/gh-ado2gh"

    git_path = shutil.which("git")
    checks["git"] = bool(git_path)
    if not git_path:
        checks["git_hint"] = "Install git in the container image (apt-get install git) and rebuild"

    if cfg.backend.value == "postgres":
        try:
            create_state_db().inventory_count()
            checks["database"] = True
        except Exception:
            checks["database"] = False
    elif cfg.backend.value == "sqlite":
        checks["database"] = True
    try:
        checks["redis"] = RedisJobQueue().ping()
    except Exception:
        checks["redis"] = False

    if strategy == "gei":
        required = ["api", "storage_backend", "database", "gh", "gh_ado2gh"]
    else:
        required = ["api", "storage_backend", "database", "git"]
    return {"ready": all(checks.get(k) for k in required), "checks": checks}


@app.post("/v1/discover", response_model=DiscoverResponse)
def discover(req: DiscoverRequest):
    active = _settings.get_active_profile()
    ado_url = active.ado_org_url if active else None
    ado_pat = active.ado_pat if active else None
    result = _accel().discover(req, ado_url=ado_url, ado_pat=ado_pat)
    return DiscoverResponse(output_dir=result.output_dir)


@app.post("/v1/plan", response_model=PlanResponse, deprecated=True, tags=["deprecated"])
def plan(req: PlanRequest):
    """Deprecated: Use POST /v1/migration/wave for wave creation and management."""
    _, waves = ConfigLoader.load(req.config_path)
    db = create_state_db(req.db_path)
    items = []
    for w in waves:
        if req.wave_id is not None and w.wave_id != req.wave_id:
            continue
        items.append({
            "wave_id": w.wave_id,
            "name": w.name,
            "repo_count": len(w.repos),
            "pipeline_count": sum(
                len(r.scopes or ["repo"]) for r in w.repos
            ),
        })
    return PlanResponse(waves=items)


@app.post("/v1/migrate", response_model=list[RunWaveResponse])
def migrate(req: RunWaveRequest, request: Request):
    try:
        active = _settings.get_active_profile()
        if active:
            try:
                assert_profile_active_for_run(active)
            except ProfileGovernanceError as exc:
                raise _governance_http_error(exc)
        user = _platform_user(request)
        profile_id = active.id if active else None
        scope_id = migrate_scope_id(profile_id, req.wave_id, req.config_path)
        if operator_requires_live_approval(user, req.dry_run):
            store = _live_store()
            approved = False
            if req.live_approval_id:
                row = store.db.get_live_execution_approval(req.live_approval_id)
                approved = bool(row and row.get("status") == "approved")
            elif store.has_approved("migrate_job", scope_id):
                approved = True
            if not approved:
                approval = store.create_or_get_pending(
                    user,
                    "migrate_job",
                    scope_id,
                    profile_id=profile_id,
                    assignment_id=req.assignment_id,
                    reason_request="Dashboard live migrate",
                    context=req.model_dump(exclude={"live_approval_id"}),
                )
                raise HTTPException(
                    status_code=403,
                    detail={"code": "awaiting_approval", "approval_id": approval["id"]},
                )
        if req.assignment_id:
            enforce_live_gate(req.assignment_id, req.dry_run, req.db_path)
        _, waves = ConfigLoader.load(req.config_path)
        targets = [w for w in waves if req.wave_id is None or w.wave_id == req.wave_id]
        if not targets:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No migration waves in migration.yaml for this request. "
                    "Use POST /v1/pipeline/runs with an active profile after Discovery phase assignment."
                ),
            )
        accel = _accel(req.db_path)
        ado_url = active.ado_org_url if active else None
        ado_pat = active.ado_pat if active else None
        gh_org = active.gh_org if active else None
        gh_token = active.github_tokens[0].token if active and active.github_tokens else None
        result = accel.run_wave(req, ado_url=ado_url, ado_pat=ado_pat, gh_token=gh_token, gh_org=gh_org)
        return [RunWaveResponse(
            wave_id=result.wave_id,
            status=result.status,
            completed=result.completed,
            failed=result.failed,
            total=result.total,
        )]
    except HTTPException:
        raise
    except Exception as exc:
        import logging
        from ado2gh.api.errors import ConfigurationError
        error_msg = str(exc)
        if "ADO_PAT" in error_msg or "ADO_ORG_URL" in error_msg:
            logging.getLogger(__name__).error("ADO credentials missing: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=400,
                detail="ADO credentials not configured. Please configure a migration profile with ADO credentials.",
            ) from exc
        if "GH_TOKEN" in error_msg:
            logging.getLogger(__name__).error("GitHub credentials missing: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=400,
                detail="GitHub credentials not configured. Please configure a migration profile with GitHub credentials.",
            ) from exc
        if isinstance(exc, ConfigurationError) or "Wave" in error_msg and "not found" in error_msg:
            raise HTTPException(status_code=400, detail=error_msg) from exc
        logging.getLogger(__name__).error("Migration failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg) from exc


@app.post("/v1/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest):
    from ado2gh.api.run_reporting import validation_repo_detail
    from ado2gh.api.validation_run import run_validation

    try:
        result = run_validation(req, _settings)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized = [validation_repo_detail(d) for d in result.details]
    matched = sum(1 for d in normalized if d.get("overall") == "PASS")
    return ValidateResponse(
        total=result.total,
        matched=matched,
        failed=result.failed,
        results=normalized,
    )


@app.post("/v1/migration/wave", response_model=RunWaveResponse)
def create_wave(req: RunWaveRequest, request: Request):
    return migrate(req, request)


def _pipeline_readiness_impl(req: ReadinessRequest) -> ReadinessResponse:
    adv = _settings.load().advanced
    db_path = req.db_path or adv.db_path
    config_path = req.config_path or adv.config_path
    db = create_state_db(db_path)
    inventory_refreshed = False

    if req.refresh_inventory or db.inventory_count() == 0:
        active = _settings.get_active_profile()
        _settings.apply_to_process_env()
        accel = _accel(db_path)
        ado_url = active.ado_org_url if active else None
        ado_pat = active.ado_pat if active else None
        accel.inventory(
            config_path,
            ado_url=ado_url,
            ado_pat=ado_pat,
        )
        inventory_refreshed = True

    migration_lookup = {}
    if hasattr(db, "get_latest_pipeline_migrations"):
        migration_lookup = db.get_latest_pipeline_migrations()
    repo_migration_lookup = {}
    if hasattr(db, "get_latest_repo_migrations"):
        repo_migration_lookup = db.get_latest_repo_migrations()

    report = PipelineReadinessReport(db).generate(
        migration_lookup=migration_lookup,
        repo_migration_lookup=repo_migration_lookup,
    )
    return ReadinessResponse(
        auto=report.get("auto", 0),
        assisted=report.get("assisted", 0),
        manual=report.get("manual", 0),
        total_pipelines=report.get("total_pipelines", 0),
        total_effort_hours=report.get("total_effort_hours", 0),
        inventory_refreshed=inventory_refreshed,
        pipelines=report.get("pipelines", []),
    )


@app.get("/v1/pipeline/readiness")
def pipeline_readiness(req: ReadinessRequest):
    return _pipeline_readiness_impl(req)


@app.post("/v1/pipeline-readiness", response_model=ReadinessResponse)
def pipeline_readiness_post(req: ReadinessRequest):
    return _pipeline_readiness_impl(req)


@app.get("/v1/migration/status")
def migration_status():
    """Per-repo migration progress from StateDB and recent pipeline runs."""
    from ado2gh.api.migration_status_report import build_migration_status_report
    from ado2gh.api.pipeline_runner import PipelineRunStore

    adv = _settings.load().advanced
    db = create_state_db(adv.db_path)
    runs, _ = PipelineRunStore.list_runs(limit=10, offset=0)
    report = build_migration_status_report(db, pipeline_runs=runs)
    return report


@app.get("/v1/dashboard", response_model=DashboardSnapshot)
def dashboard(db_path: str = "migration_state.db"):
    db = create_state_db(db_path)
    counts = db.get_migration_repo_counts()
    active = [
        ActiveMigrationItem(
            id=r.id,
            name=r.name,
            status=r.status,
            dry_run=r.dry_run,
            phase=r.phase,
            wave_id=r.wave_id,
            current_step=r.current_step_label(),
            started_by_username=r.started_by_username or "",
            started_by_display_name=r.started_by_display_name or r.started_by_username or "",
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in PipelineRunStore.list_active_runs()
    ]
    return DashboardSnapshot(
        total_repos=counts.get("total_repos", 0),
        completed_repos=counts.get("completed_repos", 0),
        failed_repos=counts.get("failed_repos", 0),
        total_pipelines=counts.get("total_pipelines", 0),
        inventory_count=db.inventory_count(),
        phase_gates=db.get_all_phase_gates(),
        active_migrations=active,
    )


@app.post("/v1/validate/freshness", response_model=FreshnessResponse)
def freshness(req: FreshnessRequest):
    global_cfg, _ = ConfigLoader.load(req.config_path)
    from ado2gh.api.accelerator import _build_ado_client, _build_gh_client

    active = _settings.get_active_profile()
    ado_url = active.ado_org_url if active else None
    ado_pat = active.ado_pat if active else None
    gh_org = active.gh_org if active else global_cfg.get("gh_org", "")
    gh_token = active.github_tokens[0].token if active and active.github_tokens else None
    ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
    gh = _build_gh_client(global_cfg, gh_token=gh_token, gh_org=gh_org)
    source = ado.get_repo(req.project, req.repo)
    repo_id = source.get("id", "")
    branch = source.get("defaultBranch", "refs/heads/main").replace("refs/heads/", "")
    commits = ado.get_repo_commits(req.project, repo_id, top=1, branch=branch)
    ado_sha = commits[0].get("commitId", "") if commits else ""
    try:
        gh_sha = gh.get_branch_sha(gh_org, req.repo, branch)
    except Exception:
        gh_sha = ""
    return FreshnessResponse(
        project=req.project,
        repo=req.repo,
        ado_head_sha=ado_sha,
        gh_head_sha=gh_sha,
        fresh=bool(ado_sha and gh_sha and ado_sha == gh_sha),
    )


@app.post("/v1/jobs", response_model=JobStatusResponse)
def enqueue_job(req: JobEnqueueRequest):
    payload = req.payload or {}
    assignment_id = payload.get("assignment_id")
    dry_run = payload.get("dry_run", True)
    if assignment_id:
        enforce_live_gate(assignment_id, dry_run, payload.get("db_path", "migration_state.db"))
    store = JobStoreFactory.from_env()
    job = store.enqueue(req.job_type, req.payload, req.idempotency_key)
    if os.environ.get("ADO2GH_LIGHTWEIGHT_MODE", "").lower() in ("1", "true", "yes"):
        store.complete(job.id, {"dry_run": dry_run, "inline": True, "status": "completed"})
        job = store.get(job.id)
        return JobStatusResponse(job=job)
    try:
        RedisJobQueue().push(job.id)
    except Exception:
        pass
    return JobStatusResponse(job=job)


@app.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str):
    job = JobStoreFactory.from_env().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(job=job)
