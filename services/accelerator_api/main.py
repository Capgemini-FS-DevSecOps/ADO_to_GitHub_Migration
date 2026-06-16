"""FastAPI REST service wrapping the Accelerator SDK."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import (
    AdvancedSettingsRequest,
    MigrationProfileRequest,
    MigrationProfileResponse,
    MigrationScanRequest,
    MigrationScanResponse,
    PhaseRecommendation,
    ProfileSetupRequest,
    DashboardSnapshot,
    DiscoveryResponse,
    DiscoveryRepoItem,
    DiscoverRequest,
    DiscoverResponse,
    FreshnessRequest,
    FreshnessResponse,
    GitHubTokenRequest,
    GitHubTokenResponse,
    HealthResponse,
    JobEnqueueRequest,
    JobStatusResponse,
    JobTypeEnum,
    PipelineRunResponse,
    PipelineRunStartRequest,
    PipelineStepDefinition,
    PhaseAssignmentRequest,
    PhaseAssignmentItem,
    PhaseDefinitionItem,
    PhasesUpdateRequest,
    PlanRequest,
    PlanResponse,
    ReadinessRequest,
    ReadinessResponse,
    RunWaveRequest,
    RunWaveResponse,
    SettingsResponse,
    ValidateAdoPatRequest,
    ValidateAdoPatResponse,
    ValidateConnectionResponse,
    ValidateGitHubTokenRequest,
    ValidateGitHubTokenResponse,
    ValidateRequest,
    ValidateResponse,
)
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
)
from ado2gh.api.settings_store import SettingsStore
from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.gh_client import GHClient
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.infra.queue.redis_queue import RedisJobQueue
from ado2gh.infra.state.job_store import JobStoreFactory
from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
from ado2gh.api.agentic_routes import enforce_live_gate, router as agentic_router

try:
    from services.accelerator_api.auth_routes import router as auth_router, SESSION_COOKIE
except ImportError:
    from auth_routes import router as auth_router, SESSION_COOKIE
from ado2gh.auth.service import AuthService, auth_enabled

app = FastAPI(title="ADO2GH Accelerator API", version="5.0.0")
app.include_router(agentic_router)
app.include_router(auth_router)

_AUTH_EXEMPT = {
    "/health",
    "/ready",
    "/v1/auth/bootstrap-status",
    "/v1/auth/bootstrap",
    "/v1/auth/login",
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


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse()


@app.get("/ready")
def ready():
    from ado2gh.infra.state.storage_config import StorageConfig

    cfg = StorageConfig.from_env()
    checks = {"api": True, "storage_backend": cfg.backend.value}
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
    required = ["api", "storage_backend", "database"]
    return {"ready": all(checks.get(k) for k in required), "checks": checks}


@app.post("/v1/discover", response_model=DiscoverResponse)
def discover(req: DiscoverRequest):
    result = _accel().discover(req)
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
    return PlanResponse(waves=items)


@app.post("/v1/migrate", response_model=list[RunWaveResponse])
def migrate(req: RunWaveRequest):
    if req.assignment_id:
        enforce_live_gate(req.assignment_id, req.dry_run, req.db_path)
    accel = _accel(req.db_path)
    result = accel.run_wave(req)
    return [RunWaveResponse(
        wave_id=result.wave_id,
        status=result.status,
        completed=result.completed,
        failed=result.failed,
        total=result.total,
    )]


@app.post("/v1/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest):
    accel = _accel(req.db_path)
    result = accel.validate(req)
    matched = sum(1 for d in result.details if d.get("overall") == "PASS")
    return ValidateResponse(
        total=result.total,
        matched=matched,
        failed=result.failed,
        results=result.details,
    )


@app.post("/v1/pipeline-readiness", response_model=ReadinessResponse)
def pipeline_readiness(req: ReadinessRequest):
    report = PipelineReadinessReport(create_state_db(req.db_path)).generate()
    by_conv = report.get("by_conversion", {})
    return ReadinessResponse(
        auto=report.get("auto", 0),
        assisted=report.get("assisted", 0),
        manual=report.get("manual", 0),
        total_effort_hours=report.get("total_effort_hours", 0),
        pipelines=report.get("pipelines", []),
    )


@app.get("/v1/dashboard", response_model=DashboardSnapshot)
def dashboard(db_path: str = "migration_state.db"):
    db = create_state_db(db_path)
    counts = db.get_migration_repo_counts()
    return DashboardSnapshot(
        total_repos=counts.get("total_repos", 0),
        completed_repos=counts.get("completed_repos", 0),
        failed_repos=counts.get("failed_repos", 0),
        total_pipelines=counts.get("total_pipelines", 0),
        inventory_count=db.inventory_count(),
        phase_gates=db.get_all_phase_gates(),
    )


@app.post("/v1/validate/freshness", response_model=FreshnessResponse)
def freshness(req: FreshnessRequest):
    global_cfg, _ = ConfigLoader.load(req.config_path)
    from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
    ado = _build_ado_client(global_cfg)
    gh = _build_gh_client(global_cfg)
    source = ado.get_repo(req.project, req.repo)
    repo_id = source.get("id", "")
    branch = source.get("defaultBranch", "refs/heads/main").replace("refs/heads/", "")
    commits = ado.get_repo_commits(req.project, repo_id, top=1, branch=branch)
    ado_sha = commits[0].get("commitId", "") if commits else ""
    gh_org = global_cfg.get("gh_org", "")
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


@app.get("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def get_migration_profile(profile_id: str):
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@app.post("/v1/settings/profiles", response_model=MigrationProfileResponse)
def create_profile(req: MigrationProfileRequest):
    p = _settings.upsert_profile(req.model_dump())
    return MigrationProfileResponse(**p.to_public())


@app.post("/v1/settings/profiles/setup", response_model=MigrationProfileResponse)
def setup_profile(req: ProfileSetupRequest):
    ado_result = validate_ado_pat(req.ado_org_url, req.ado_pat)
    if not ado_result["valid"]:
        raise HTTPException(status_code=400, detail=ado_result["message"])
    gh_result = validate_github_token(req.github_token, req.gh_org)
    if not gh_result["valid"]:
        raise HTTPException(status_code=400, detail=gh_result["message"])
    p = _settings.setup_profile(req.model_dump())
    _settings.apply_to_process_env(p)
    try:
        raw = scan_with_credentials(
            p.ado_org_url, p.ado_pat, gh_org=p.gh_org,
            phase_definitions=[ph.to_dict() for ph in _settings.get_phases()],
        )
        persist_scan_results(p.id, raw)
        _settings.record_scan_summary(p.id, raw)
    except Exception as exc:
        # Profile is still usable; discovery can be re-run from the Discovery tab.
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
    p = _require_profile(profile_id)
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


@app.get("/v1/settings/profiles/{profile_id}/scan")
def get_profile_scan(profile_id: str):
    _require_profile(profile_id)
    data = load_scan_results(profile_id)
    if not data:
        raise HTTPException(status_code=404, detail="No scan results for this profile")
    return _scan_response(data)


@app.put("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def update_profile(profile_id: str, req: MigrationProfileRequest):
    try:
        p = _settings.upsert_profile(req.model_dump(), profile_id=profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@app.delete("/v1/settings/profiles/{profile_id}")
def delete_profile(profile_id: str):
    _settings.delete_profile(profile_id)
    return {"deleted": profile_id}


@app.post("/v1/settings/profiles/{profile_id}/activate")
def activate_profile(profile_id: str):
    try:
        _settings.set_active(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    _settings.apply_to_process_env()
    return {"active_profile_id": profile_id}


def _require_profile(profile_id: str):
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
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
def create_github_token(profile_id: str, req: GitHubTokenRequest):
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return GitHubTokenResponse(**t.to_public())


@app.put("/v1/settings/profiles/{profile_id}/tokens/{token_id}", response_model=GitHubTokenResponse)
def update_github_token(profile_id: str, token_id: str, req: GitHubTokenRequest):
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump(), token_id=token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return GitHubTokenResponse(**t.to_public())


@app.delete("/v1/settings/profiles/{profile_id}/tokens/{token_id}")
def delete_github_token(profile_id: str, token_id: str):
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
    _require_profile(profile_id)
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
def update_phase_assignments(profile_id: str, req: PhaseAssignmentRequest):
    _require_profile(profile_id)
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    if not hasattr(db, "update_profile_repo_phases"):
        raise HTTPException(status_code=501, detail="Phase assignment not supported on this storage backend")
    updates = [a.model_dump() for a in req.assignments]
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
def list_pipeline_runs(limit: int = 50):
    return {"runs": [r.to_dict() for r in PipelineRunStore.list_runs(limit)]}


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
def start_pipeline_run(req: PipelineRunStartRequest):
    adv = _settings.load().advanced
    dry = req.dry_run if req.dry_run is not None else adv.dry_run_default
    _settings.apply_to_process_env()
    run = PipelineRunStore.create(
        req.name, dry, req.phase, req.wave_id,
        step_defs=MIGRATE_UI_PIPELINE_STEPS,
    )
    _runner.start_async(run.id, req.steps)
    return PipelineRunResponse(run=run.to_dict())
