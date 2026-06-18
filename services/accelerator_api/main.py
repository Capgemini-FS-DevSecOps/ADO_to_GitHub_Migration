"""FastAPI REST service wrapping the Accelerator SDK."""
from __future__ import annotations

import os

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import *
from ado2gh.api.credential_validation import validate_ado_pat, validate_github_token
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
    resolve_pipeline_step_defs,
)
from ado2gh.api.settings_store import SettingsStore
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.infra.queue.redis_queue import RedisJobQueue
from ado2gh.infra.state.job_store import JobStoreFactory
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

app = FastAPI(title="ADO2GH Accelerator API", version="5.1.0")
app.include_router(agentic_router)
app.include_router(auth_router)

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


def _accel(db_path: str = "migration_state.db") -> Accelerator:
    return Accelerator(db_path=db_path)


def _config_path() -> str:
    return os.environ.get("ADO2GH_CONFIG", "migration.yaml")


def _platform_user(request: Request):
    return getattr(request.state, "platform_user", None)


def _require_admin(request: Request) -> None:
    require_manage_settings(request)


def _live_store() -> LiveApprovalStore:
    return LiveApprovalStore()


def _execute_approved_migrate(ctx: dict) -> None:
    req = RunWaveRequest(**ctx)
    _accel(req.db_path).run_wave(req)


def _execute_approved_pipeline(ctx: dict) -> None:
    run_id = ctx.get("run_id")
    steps = ctx.get("steps")
    if run_id:
        run = PipelineRunStore.get(run_id)
        if run:
            run.status = "pending"
        _runner.start_async(run_id, steps)


def _onboarding_redirect() -> str | None:
    from ado2gh.api.profile_governance import needs_profile_setup

    profiles = _settings.load().migration_profiles
    if needs_profile_setup(profiles):
        return "/onboarding/profile"
    return None


def _governance_http_error(exc: ProfileGovernanceError) -> HTTPException:
    code = exc.code
    status = 409 if code in ("last_active_profile", "default_replacement_required") else 403
    if code == "not_found":
        status = 404
    return HTTPException(status_code=status, detail=code)


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

    from ado2gh.infra.state.storage_config import StorageConfig
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
            result = subprocess.run(
                ["gh", "extension", "list"],
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


@app.post("/v1/plan", response_model=PlanResponse)
def plan(req: PlanRequest):
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
                db.inventory_count_for_repo(r.ado_project, r.ado_repo) for r in w.repos
            ),
        })
    if not items:
        active = _settings.get_active_profile()
        if active:
            from ado2gh.api.migration_scan import load_scan_results
            from ado2gh.api.profile_discovery import iter_scan_repos

            scan = load_scan_results(active.id)
            if scan:
                by_phase: dict[str, list] = {}
                for repo in iter_scan_repos(scan):
                    ph = repo.get("assigned_phase") or repo.get("suggested_phase") or "poc"
                    by_phase.setdefault(ph, []).append(repo)
                for idx, (ph, repos) in enumerate(sorted(by_phase.items())):
                    items.append({
                        "wave_id": 9000 + idx,
                        "name": ph,
                        "phase": ph,
                        "repo_count": len(repos),
                        "pipeline_count": sum(
                            int(r.get("pipeline_count", 0)) for r in repos
                        ),
                        "source": "profile_discovery",
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
        # Extract credentials from active profile if available
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
                detail="ADO credentials not configured. Please configure a migration profile with ADO credentials."
            ) from exc
        if "GH_TOKEN" in error_msg:
            logging.getLogger(__name__).error("GitHub credentials missing: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=400,
                detail="GitHub credentials not configured. Please configure a migration profile with GitHub credentials."
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


@app.post("/v1/pipeline-readiness", response_model=ReadinessResponse)
def pipeline_readiness(req: ReadinessRequest):
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

    report = PipelineReadinessReport(db).generate(migration_lookup=migration_lookup)
    return ReadinessResponse(
        auto=report.get("auto", 0),
        assisted=report.get("assisted", 0),
        manual=report.get("manual", 0),
        total_pipelines=report.get("total_pipelines", 0),
        total_effort_hours=report.get("total_effort_hours", 0),
        inventory_refreshed=inventory_refreshed,
        pipelines=report.get("pipelines", []),
    )


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


# ── Settings & manual pipeline (UI accelerator) ─────────────────────────────

_settings = SettingsStore()
_runner = PipelineRunner(_settings)
_llm_models = __import__("ado2gh.api.llm_model_store", fromlist=["LLMModelStore"]).LLMModelStore()
_connectivity = __import__(
    "ado2gh.api.connectivity_store", fromlist=["ConnectivityStore"]
).ConnectivityStore()

register_migrate_executor(_execute_approved_migrate)
register_pipeline_executor(_execute_approved_pipeline)


def _maybe_audit_model_enabled(
    request: Request,
    *,
    before_enabled: bool,
    before_default: bool,
    model,
) -> None:
    if model.validation_status != "passed":
        return
    user = _platform_user(request)
    if model.enabled != before_enabled or model.default_for_agent != before_default:
        if model.enabled or model.default_for_agent:
            write_profile_audit(
                "llm.model.enabled",
                profile_id="_platform",
                actor=user.username if user else "admin",
                payload={
                    "model_id": model.id,
                    "enabled": model.enabled,
                    "default_for_agent": model.default_for_agent,
                },
            )


@app.get("/v1/settings/connectivity")
def get_connectivity(request: Request):
    require_manage_models(request)
    return _connectivity.load().to_public()


@app.put("/v1/settings/connectivity")
def put_connectivity(request: Request, body: dict):
    require_manage_models(request)
    before = _connectivity.load().to_public()
    user = _platform_user(request)
    actor = user.username if user else "admin"
    profile = _connectivity.update(body, actor=actor)
    after = profile.to_public()
    changed: list[str] = []
    for key in body:
        if key in ("proxy_password", "custom_ca_pem"):
            if body[key] not in (None, "", "***"):
                changed.append(key)
        elif before.get(key) != after.get(key):
            changed.append(key)
    write_profile_audit(
        "connectivity.updated",
        profile_id="_platform",
        actor=actor,
        payload={"fields": changed},
    )
    return after


@app.post("/v1/settings/connectivity/test")
def test_connectivity_route(request: Request):
    require_manage_models(request)
    from ado2gh.api.http_llm import build_llm_http_client
    from ado2gh.api.model_validation import _classify_error

    try:
        with build_llm_http_client(for_cloud=True) as client:
            response = client.get("https://api.openai.com/v1/models")
            response.raise_for_status()
        return {
            "status": "passed",
            "category": None,
            "message": "Outbound TLS and proxy path succeeded.",
        }
    except Exception as exc:
        category, message = _classify_error(exc)
        return {"status": "failed", "category": category, "message": message}


@app.get("/v1/settings/llm-models/catalog")
def get_llm_catalog(
    request: Request,
    provider: str,
    api_key: str = "",
    base_url: str = "",
):
    require_manage_models(request)
    from ado2gh.api.model_catalog import list_catalog

    try:
        return list_catalog(provider=provider, api_key=api_key, base_url=base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/settings/llm-models/validate")
def validate_llm_draft(request: Request, body: dict):
    require_manage_models(request)
    from ado2gh.api.model_validation import validate_draft

    result = validate_draft(body)
    user = _platform_user(request)
    write_profile_audit(
        "llm.model.validated",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={"status": result["status"], "category": result.get("category")},
    )
    return result


@app.post("/v1/settings/llm-models/{model_id}/validate")
def validate_llm_saved(model_id: str, request: Request):
    require_manage_models(request)
    from ado2gh.api.model_validation import validate_saved

    try:
        result = validate_saved(model_id, store=_llm_models)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Model not found") from exc
    user = _platform_user(request)
    write_profile_audit(
        "llm.model.validated",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={
            "status": result["status"],
            "category": result.get("category"),
            "model_id": model_id,
        },
    )
    return result


@app.get("/v1/settings/llm-models")
def list_llm_models(request: Request):
    user = _platform_user(request)
    if user and user.role == PlatformRole.ADMIN:
        return {"models": [m.to_public() for m in _llm_models.load()]}
    return {"models": _llm_models.list_public()}


@app.post("/v1/settings/llm-models")
def create_llm_model(request: Request, body: dict):
    require_manage_models(request)
    try:
        model = _llm_models.upsert(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = _platform_user(request)
    write_profile_audit(
        "llm_model.created",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={"model_id": model.id, "provider": model.provider},
    )
    if model.enabled or model.default_for_agent:
        write_profile_audit(
            "llm.model.enabled",
            profile_id="_platform",
            actor=user.username if user else "admin",
            payload={
                "model_id": model.id,
                "enabled": model.enabled,
                "default_for_agent": model.default_for_agent,
            },
        )
    return model.to_public()


@app.put("/v1/settings/llm-models/{model_id}")
def update_llm_model(model_id: str, request: Request, body: dict):
    require_manage_models(request)
    existing = _llm_models.get(model_id)
    before_enabled = existing.enabled if existing else False
    before_default = existing.default_for_agent if existing else False
    try:
        model = _llm_models.upsert(body, model_id=model_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Model not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _maybe_audit_model_enabled(
        request,
        before_enabled=before_enabled,
        before_default=before_default,
        model=model,
    )
    return model.to_public()


@app.delete("/v1/settings/llm-models/{model_id}")
def delete_llm_model(model_id: str, request: Request):
    require_manage_models(request)
    existing = _llm_models.get(model_id)
    if not existing:
        raise HTTPException(status_code=404, detail="model_not_found")
    _llm_models.delete(model_id)
    user = _platform_user(request)
    write_profile_audit(
        "llm.model.deleted",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={"model_id": model_id, "display_name": existing.display_name},
    )
    return {"deleted": model_id}


@app.get("/v1/settings", response_model=SettingsResponse)
def get_settings():
    s = _settings.load()
    return SettingsResponse(**s.to_public())


@app.put("/v1/settings/advanced")
def update_advanced(req: AdvancedSettingsRequest):
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    adv = _settings.update_advanced(data)
    return asdict_adv(adv)


@app.get("/v1/settings/phases")
def get_phases(profile_id: str | None = None):
    return _settings.phases_payload(profile_id)


@app.put("/v1/settings/phases")
def update_phases(req: PhasesUpdateRequest):
    try:
        return _settings.update_phases(
            [p.model_dump() for p in req.phases],
            removals=[r.model_dump() for r in req.removals],
            span_to_scan=req.span_to_scan,
            profile_id=req.profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

def asdict_adv(adv):
    from dataclasses import asdict
    return asdict(adv)


@app.get("/v1/settings/profiles/pending", response_model=list[MigrationProfileResponse])
def list_pending_profiles(request: Request):
    _require_admin(request)
    pending = [
        p for p in _settings.load().migration_profiles
        if p.status == "pending_approval"
    ]
    return [MigrationProfileResponse(**p.to_public()) for p in pending]


@app.get("/v1/settings/profiles/mine/pending", response_model=list[MigrationProfileResponse])
def list_my_pending_profiles(request: Request):
    user = _platform_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    mine = [
        p for p in _settings.load().migration_profiles
        if p.submitted_by == user.username and p.status in ("pending_approval", "denied")
    ]
    return [MigrationProfileResponse(**p.to_public()) for p in mine]


@app.get("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def get_migration_profile(profile_id: str):
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@app.post("/v1/settings/profiles", response_model=MigrationProfileResponse)
def create_profile(req: MigrationProfileRequest, request: Request):
    require_manage_settings(request)
    p = _settings.upsert_profile(req.model_dump())
    return MigrationProfileResponse(**p.to_public())


@app.post("/v1/settings/profiles/setup", response_model=MigrationProfileResponse)
def setup_profile(req: ProfileSetupRequest, request: Request):
    user = _platform_user(request)
    role = user.role.value if user else PlatformRole.ADMIN.value
    profiles = _settings.load().migration_profiles
    if user:
        try:
            assert_operator_can_submit(profiles, user.role)
        except ProfileGovernanceError as exc:
            raise HTTPException(status_code=403, detail=exc.code)

    ado_result = validate_ado_pat(req.ado_org_url, req.ado_pat)
    if not ado_result["valid"]:
        if user:
            write_profile_audit(
                "profile.validation_failed",
                profile_id="_pending",
                actor=user.username,
                payload={"step": "ado", "message": ado_result["message"]},
            )
        raise HTTPException(status_code=400, detail=ado_result["message"])
    gh_result = validate_github_token(req.github_token, req.gh_org)
    if not gh_result["valid"]:
        if user:
            write_profile_audit(
                "profile.validation_failed",
                profile_id="_pending",
                actor=user.username,
                payload={"step": "github", "message": gh_result["message"]},
            )
        raise HTTPException(status_code=400, detail=gh_result["message"])
    try:
        p = _settings.setup_profile(
            req.model_dump(),
            role=role,
            submitted_by=user.username if user else "",
        )
    except ValueError as exc:
        if str(exc) == "operator_submit_blocked":
            raise HTTPException(status_code=403, detail="operator_submit_blocked") from exc
        raise
    actor = user.username if user else "system"
    event = "profile.created" if p.status == "active" else "profile.submitted"
    write_profile_audit(event, profile_id=p.id, actor=actor, payload={"status": p.status})
    if p.status == "active":
        _settings.apply_to_process_env(p)
        try:
            raw = scan_with_credentials(
                p.ado_org_url, p.ado_pat, gh_org=p.gh_org,
                phase_definitions=[ph.to_dict() for ph in _settings.get_phases()],
            )
            persist_scan_results(p.id, raw)
            _settings.record_scan_summary(p.id, raw)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Profile setup scan failed: %s", exc)
    return MigrationProfileResponse(**p.to_public())


def _scan_response(raw: dict) -> MigrationScanResponse:
    recs = {
        phase: PhaseRecommendation(**bucket)
        for phase, bucket in raw.get("recommendations", {}).items()
    }
    return MigrationScanResponse(
        scanned_at=raw.get("scanned_at", ""),
        projects_scanned=raw.get("projects_scanned", 0),
        repos_scanned=raw.get("repos_scanned", 0),
        total_repos=raw.get("total_repos", 0),
        gh_org=raw.get("gh_org", ""),
        recommendations=recs,
        project_details=raw.get("project_details", []),
        warnings=raw.get("warnings", []),
        status=raw.get("status", "ok"),
    )


@app.post("/v1/migration/scan", response_model=MigrationScanResponse)
def migration_scan_inline(req: MigrationScanRequest):
    if not req.ado_org_url or not req.ado_pat:
        raise HTTPException(status_code=400, detail="ADO org URL and PAT are required")
    ado_result = validate_ado_pat(req.ado_org_url, req.ado_pat)
    if not ado_result["valid"]:
        raise HTTPException(status_code=400, detail=ado_result["message"])
    try:
        raw = scan_with_credentials(
            req.ado_org_url, req.ado_pat, gh_org=req.gh_org, max_repos=req.max_repos,
            phase_definitions=[p.to_dict() for p in _settings.get_phases()],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _scan_response(raw)


@app.post("/v1/settings/profiles/{profile_id}/scan", response_model=MigrationScanResponse)
def migration_scan_profile(profile_id: str, max_repos: int | None = None):
    p = _require_profile(profile_id, require_active=True)
    if not p.ado_org_url or not p.ado_pat:
        raise HTTPException(status_code=400, detail="Profile missing ADO credentials")
    try:
        from ado2gh.api.profile_discovery import resolve_gh_org
        adv = _settings.load().advanced
        gh_org = resolve_gh_org(p, config_path=adv.config_path)
        raw = scan_with_credentials(
            p.ado_org_url, p.ado_pat, gh_org=gh_org, max_repos=max_repos,
            phase_definitions=[ph.to_dict() for ph in _settings.get_phases()],
        )
        if gh_org and not raw.get("gh_org"):
            raw["gh_org"] = gh_org
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    persist_scan_results(profile_id, raw)
    _settings.record_scan_summary(profile_id, raw)
    from ado2gh.api.profile_discovery import sync_profile_scan_to_risk_scores
    sync_profile_scan_to_risk_scores(profile_id, raw, config_path=adv.config_path)
    return _scan_response(raw)


@app.get("/v1/settings/profiles/{profile_id}/scan/status")
def profile_scan_status(profile_id: str):
    _require_profile(profile_id)
    return _settings.profile_rescan_status(profile_id)


@app.get("/v1/settings/profiles/{profile_id}/scan")
def get_profile_scan(profile_id: str):
    _require_profile(profile_id)
    data = load_scan_results(profile_id)
    if not data:
        raise HTTPException(status_code=404, detail="No scan results for this profile")
    return _scan_response(data)


@app.put("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def update_profile(profile_id: str, req: MigrationProfileRequest, request: Request):
    require_manage_settings(request)
    try:
        p = _settings.upsert_profile(req.model_dump(), profile_id=profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@app.delete("/v1/settings/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request, body: DeleteProfileRequest | None = None):
    _require_admin(request)
    user = _platform_user(request)
    try:
        _settings.delete_profile(profile_id, body.new_default_profile_id if body else None)
    except ValueError as exc:
        code = str(exc)
        if code == "last_active_profile":
            raise HTTPException(status_code=409, detail=code) from exc
        if code in ("default_replacement_required", "invalid_default_replacement"):
            raise HTTPException(status_code=400, detail=code) from exc
        raise
    write_profile_audit(
        "profile.deleted",
        profile_id=profile_id,
        actor=user.username if user else "admin",
    )
    return {"deleted": profile_id}


@app.post("/v1/settings/profiles/{profile_id}/set-default")
def set_profile_default(profile_id: str, request: Request):
    _require_admin(request)
    try:
        p = _settings.set_default_profile(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    user = _platform_user(request)
    write_profile_audit(
        "profile.default_changed",
        profile_id=profile_id,
        actor=user.username if user else "admin",
    )
    return MigrationProfileResponse(**p.to_public())


@app.post("/v1/settings/profiles/{profile_id}/deactivate", response_model=MigrationProfileResponse)
def deactivate_profile(profile_id: str, request: Request, body: DeleteProfileRequest | None = None):
    _require_admin(request)
    user = _platform_user(request)
    try:
        p = _settings.deactivate_profile(profile_id, body.new_default_profile_id if body else None)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        code = str(exc)
        if code == "last_active_profile":
            raise HTTPException(status_code=409, detail=code) from exc
        raise HTTPException(status_code=400, detail=code) from exc
    write_profile_audit(
        "profile.deactivated",
        profile_id=profile_id,
        actor=user.username if user else "admin",
    )
    return MigrationProfileResponse(**p.to_public())


@app.post("/v1/settings/profiles/{profile_id}/approve", response_model=MigrationProfileResponse)
def approve_profile(profile_id: str, request: Request):
    _require_admin(request)
    p = _require_profile(profile_id, require_active=False)
    ado_result = validate_ado_pat(p.ado_org_url, p.ado_pat)
    if not ado_result["valid"]:
        raise HTTPException(status_code=400, detail=ado_result["message"])
    if p.github_tokens:
        gh_result = validate_github_token(p.github_tokens[0].token, p.gh_org)
        if not gh_result["valid"]:
            raise HTTPException(status_code=400, detail=gh_result["message"])
    try:
        approved = _settings.approve_profile(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = _platform_user(request)
    write_profile_audit(
        "profile.approved",
        profile_id=profile_id,
        actor=user.username if user else "admin",
    )
    return MigrationProfileResponse(**approved.to_public())


@app.post("/v1/settings/profiles/{profile_id}/deny", response_model=MigrationProfileResponse)
def deny_profile(profile_id: str, request: Request, body: DenyProfileRequest | None = None):
    _require_admin(request)
    try:
        denied = _settings.deny_profile(profile_id, body.reason if body else "")
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = _platform_user(request)
    write_profile_audit(
        "profile.denied",
        profile_id=profile_id,
        actor=user.username if user else "admin",
        payload={"reason": body.reason if body else ""},
    )
    return MigrationProfileResponse(**denied.to_public())


@app.post("/v1/settings/profiles/{profile_id}/appeal", response_model=MigrationProfileResponse)
def appeal_profile(profile_id: str, request: Request):
    user = _platform_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        appealed = _settings.appeal_profile(profile_id, user.username)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError:
        raise HTTPException(status_code=403, detail="not_submitter")
    write_profile_audit(
        "profile.appealed",
        profile_id=profile_id,
        actor=user.username,
    )
    return MigrationProfileResponse(**appealed.to_public())


@app.post("/v1/settings/profiles/{profile_id}/activate")
def activate_profile(profile_id: str):
    try:
        _settings.set_active(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    _settings.apply_to_process_env()
    return {"active_profile_id": profile_id}


def _require_profile(profile_id: str, require_active: bool = False):
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    if require_active:
        try:
            assert_profile_active_for_run(p)
        except ProfileGovernanceError as exc:
            raise _governance_http_error(exc)
    return p


@app.post("/v1/settings/profiles/{profile_id}/validate/source", response_model=ValidateAdoPatResponse)
def validate_profile_source(profile_id: str):
    p = _require_profile(profile_id)
    result = validate_ado_pat(p.ado_org_url, p.ado_pat)
    return ValidateAdoPatResponse(**result)


@app.post("/v1/settings/profiles/{profile_id}/validate", response_model=ValidateConnectionResponse)
def validate_migration_profile(profile_id: str):
    p = _require_profile(profile_id)
    ado_result = validate_ado_pat(p.ado_org_url, p.ado_pat)
    if not ado_result["valid"]:
        return ValidateConnectionResponse(valid=False, message=ado_result["message"])

    if not p.github_tokens:
        return ValidateConnectionResponse(
            valid=False,
            message="Source ADO OK — add GitHub tokens for this migration profile",
            ado_projects=ado_result["ado_projects"],
        )

    gh_result = validate_github_token(p.github_tokens[0].token, p.gh_org)
    if not gh_result["valid"]:
        return ValidateConnectionResponse(
            valid=False,
            message=f"ADO OK; GitHub ({p.gh_org}): {gh_result['message']}",
            ado_projects=ado_result["ado_projects"],
        )

    return ValidateConnectionResponse(
        valid=True,
        ado_projects=ado_result["ado_projects"],
        gh_token_remaining=gh_result.get("remaining", 0),
        message=(
            f"{p.name}: {ado_result['ado_projects']} ADO projects → "
            f"GitHub org {p.gh_org} as {gh_result.get('login', 'user')}"
        ),
    )


@app.post("/v1/settings/validate", response_model=ValidateConnectionResponse)
def validate_connection():
    profile = _settings.get_active_profile()
    if not profile:
        return ValidateConnectionResponse(valid=False, message="No active migration profile")
    return validate_migration_profile(profile.id)


@app.post("/v1/settings/profiles/{profile_id}/validate/ado", response_model=ValidateAdoPatResponse)
def validate_ado_for_profile(profile_id: str, req: ValidateAdoPatRequest):
    p = _require_profile(profile_id)
    ado_org = req.ado_org_url or p.ado_org_url
    ado_pat = req.ado_pat if req.ado_pat and req.ado_pat != "***" else p.ado_pat
    result = validate_ado_pat(ado_org, ado_pat)
    return ValidateAdoPatResponse(**result)


@app.get("/v1/settings/profiles/{profile_id}/tokens", response_model=list[GitHubTokenResponse])
def list_github_tokens(profile_id: str):
    p = _require_profile(profile_id)
    return [GitHubTokenResponse(**t.to_public()) for t in p.github_tokens]


@app.post("/v1/settings/profiles/{profile_id}/tokens", response_model=GitHubTokenResponse)
def create_github_token(profile_id: str, req: GitHubTokenRequest, request: Request):
    require_manage_settings(request)
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return GitHubTokenResponse(**t.to_public())


@app.put("/v1/settings/profiles/{profile_id}/tokens/{token_id}", response_model=GitHubTokenResponse)
def update_github_token(profile_id: str, token_id: str, req: GitHubTokenRequest, request: Request):
    require_manage_settings(request)
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump(), token_id=token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return GitHubTokenResponse(**t.to_public())


@app.delete("/v1/settings/profiles/{profile_id}/tokens/{token_id}")
def delete_github_token(profile_id: str, token_id: str, request: Request):
    require_manage_settings(request)
    try:
        _settings.delete_github_token(profile_id, token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return {"deleted": token_id}


@app.post("/v1/settings/profiles/{profile_id}/tokens/validate", response_model=ValidateGitHubTokenResponse)
def validate_github_token_inline(profile_id: str, req: ValidateGitHubTokenRequest):
    p = _require_profile(profile_id)
    result = validate_github_token(req.token, p.gh_org)
    return ValidateGitHubTokenResponse(**result)


@app.post("/v1/settings/profiles/{profile_id}/tokens/{token_id}/validate", response_model=ValidateGitHubTokenResponse)
def validate_saved_github_token(profile_id: str, token_id: str):
    p = _require_profile(profile_id)
    tok = _settings.get_github_token(profile_id, token_id)
    if not tok:
        raise HTTPException(status_code=404, detail="Token not found")
    result = validate_github_token(tok.token, p.gh_org)
    _settings.record_token_validation(profile_id, token_id, result)
    return ValidateGitHubTokenResponse(**result)


@app.post("/v1/settings/validate/ado", response_model=ValidateAdoPatResponse)
def validate_ado_inline(req: ValidateAdoPatRequest):
    result = validate_ado_pat(req.ado_org_url, req.ado_pat)
    return ValidateAdoPatResponse(**result)


@app.post("/v1/settings/validate/github", response_model=ValidateGitHubTokenResponse)
def validate_github_inline(req: ValidateGitHubTokenRequest):
    result = validate_github_token(req.token, req.gh_org)
    return ValidateGitHubTokenResponse(**result)


@app.get("/v1/settings/profiles/{profile_id}/discovery", response_model=DiscoveryResponse)
def get_profile_discovery(profile_id: str):
    _require_profile(profile_id, require_active=True)
    data = load_scan_results(profile_id)
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    repos: list[DiscoveryRepoItem] = []
    if data and hasattr(db, "get_profile_scan_repos"):
        for r in db.get_profile_scan_repos(profile_id):
            repos.append(DiscoveryRepoItem(
                project=r["project"],
                repo_name=r["repo_name"],
                total_score=r["total_score"],
                suggested_phase=r.get("suggested_phase"),
                assigned_phase=r.get("assigned_phase"),
                gh_org=r.get("gh_org", ""),
                gh_repo=r.get("gh_repo", ""),
                pipeline_count=r.get("pipeline_count", 0),
            ))
    if not repos and data:
        for bucket in data.get("recommendations", {}).values():
            for repo in bucket.get("repos", []):
                repos.append(DiscoveryRepoItem(
                    project=repo.get("project", ""),
                    repo_name=repo.get("repo_name", ""),
                    total_score=repo.get("total_score", 0),
                    suggested_phase=repo.get("assigned_phase"),
                    assigned_phase=repo.get("assigned_phase"),
                    gh_org=repo.get("gh_org", ""),
                    gh_repo=repo.get("gh_repo", ""),
                    pipeline_count=repo.get("pipeline_count", 0),
                ))
    return DiscoveryResponse(
        profile_id=profile_id,
        scanned_at=data.get("scanned_at", "") if data else "",
        gh_org=data.get("gh_org", "") if data else "",
        repos_scanned=data.get("repos_scanned", len(repos)) if data else 0,
        projects_scanned=data.get("projects_scanned", 0) if data else 0,
        repos=repos,
        recommendations=data.get("recommendations", {}) if data else {},
        project_details=data.get("project_details", []) if data else [],
        warnings=data.get("warnings", []) if data else [],
        status=data.get("status", "ok") if data else "empty",
    )


@app.put("/v1/settings/profiles/{profile_id}/phase-assignments")
def update_phase_assignments(profile_id: str, req: PhaseAssignmentRequest, request: Request):
    require_manage_settings(request)
    _require_profile(profile_id)
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    if not hasattr(db, "update_profile_repo_phases"):
        raise HTTPException(status_code=501, detail="Phase assignment not supported on this storage backend")
    updates = [a.model_dump() for a in req.assignments]
    count = db.update_profile_repo_phases(profile_id, updates)
    if count == 0 and updates and hasattr(db, "save_profile_scan"):
        # Scans from before Postgres profile_scan tables may exist only as JSON backups.
        if not db.get_profile_scan_repos(profile_id):
            cached = load_scan_results(profile_id)
            if cached and cached.get("recommendations"):
                db.save_profile_scan(profile_id, cached)
                count = db.update_profile_repo_phases(profile_id, updates)
    if count == 0 and updates:
        raise HTTPException(status_code=404, detail="No matching repos found to update")
    from ado2gh.api.profile_discovery import sync_profile_scan_to_risk_scores
    adv = _settings.load().advanced
    sync_profile_scan_to_risk_scores(profile_id, config_path=adv.config_path)
    return {"updated": count, "scan": load_scan_results(profile_id)}


@app.get("/v1/pipeline/steps", response_model=list[PipelineStepDefinition])
def pipeline_steps(context: str = "migrate"):
    steps = MIGRATE_UI_PIPELINE_STEPS if context == "migrate" else ACCELERATOR_PIPELINE_STEPS
    return [PipelineStepDefinition(**s) for s in steps]


@app.get("/v1/pipeline/runs")
def list_pipeline_runs(limit: int = 20, offset: int = 0):
    runs, total = PipelineRunStore.list_runs(limit=limit, offset=offset)
    return {
        "runs": [r.to_dict() for r in runs],
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": PipelineRunStore.summary(),
    }


@app.get("/v1/pipeline/runs/{run_id}")
def get_pipeline_run(run_id: str):
    run = PipelineRunStore.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run.to_dict()}


@app.post("/v1/pipeline/runs/{run_id}/cancel")
def cancel_pipeline_run(run_id: str):
    if not _runner.cancel(run_id):
        run = PipelineRunStore.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")
    run = PipelineRunStore.get(run_id)
    return {"run": run.to_dict() if run else None, "cancelled": True}


@app.post("/v1/pipeline/runs", response_model=PipelineRunResponse)
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
        started_by_username=getattr(user, "username", None) if user else None,
        started_by_display_name=(
            getattr(user, "display_name", None) or getattr(user, "username", None)
        ) if user else None,
    )
    if operator_requires_live_approval(user, dry):
        store = _live_store()
        store.create_or_get_pending(
            user,
            "pipeline_run",
            run.id,
            profile_id=active.id,
            reason_request=f"Pipeline live run: {req.name}",
            context={"run_id": run.id, "steps": step_ids},
        )
        run.status = "awaiting_approval"
        run.updated_at = run.created_at
        if run.steps:
            run.steps[0].message = "Waiting for live execution approval"
        return PipelineRunResponse(run=run.to_dict())
    _runner.start_async(run.id, step_ids)
    return PipelineRunResponse(run=run.to_dict())


# ── Unified live execution approval queue ────────────────────────────────────


@app.get("/v1/platform/approvals", response_model=LiveApprovalListResponse)
def list_live_approvals(request: Request, status: str = "pending"):
    require_approve_live_execution(request)
    store = _live_store()
    items = [LiveApprovalItem(**row) for row in store.list_approvals(status=status)]
    return LiveApprovalListResponse(approvals=items)


@app.get("/v1/platform/approvals/{approval_id}", response_model=LiveApprovalItem)
def get_live_approval(approval_id: str, request: Request):
    user = require_operate(request)
    store = _live_store()
    row = store.get_approval(approval_id, requester=user)
    return LiveApprovalItem(**row)


@app.post("/v1/platform/approvals", response_model=LiveApprovalItem)
def create_live_approval(req: LiveApprovalCreateRequest, request: Request):
    user = require_operate(request)
    store = _live_store()
    row = store.create_or_get_pending(
        user,
        req.scope_type,
        req.scope_id,
        profile_id=req.profile_id,
        assignment_id=req.assignment_id,
        reason_request=req.reason_request,
        context=req.context,
    )
    return LiveApprovalItem(**row)


@app.post("/v1/platform/approvals/{approval_id}/approve", response_model=LiveApprovalItem)
def approve_live_execution(
    approval_id: str, req: LiveApprovalDecisionRequest, request: Request,
):
    approver = require_approve_live_execution(request)
    store = _live_store()
    row = store.approve(approval_id, approver, req.reason)
    return LiveApprovalItem(**row)


@app.post("/v1/platform/approvals/{approval_id}/deny", response_model=LiveApprovalItem)
def deny_live_execution(
    approval_id: str, req: LiveApprovalDecisionRequest, request: Request,
):
    approver = require_approve_live_execution(request)
    store = _live_store()
    row = store.deny(approval_id, approver, req.reason)
    return LiveApprovalItem(**row)
