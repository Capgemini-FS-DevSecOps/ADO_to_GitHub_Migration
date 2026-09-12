"""FastAPI REST service wrapping the Accelerator SDK.

Hosts the Accelerator's HTTP surface on port 8080: the health and readiness
probes, the discovery, migrate, validate, readiness, dashboard and job
endpoints defined here, and the routers mounted from ``ado2gh.api`` and
``services.accelerator_api.routes``. The Next.js console and the agent service
are both clients. Requests to ``/v1/`` are gated by
:func:`platform_auth_middleware`, which resolves the session cookie into a
platform user before any handler runs.

The module path ``services.accelerator_api.main:app`` is load-bearing — the
Dockerfile, both compose files, the Kubernetes manifests and CI all name it.
"""
# ruff: noqa: E402  -- imports below intentionally follow ensure_gei_dotnet_env()
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ado2gh.core.gei_runtime import ensure_gei_dotnet_env

ensure_gei_dotnet_env()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from ado2gh.api.contracts import (
    ActiveMigrationItem,
    DashboardSnapshot,
    DiscoverRequest,
    DiscoverResponse,
    FreshnessRequest,
    FreshnessResponse,
    HealthResponse,
    JobEnqueueRequest,
    JobStatusResponse,
    LiveApprovalCreateRequest,
    OnboardingStatusResponse,
    PlanRequest,
    PlanResponse,
    ReadinessRequest,
    ReadinessResponse,
    RunWaveRequest,
    RunWaveResponse,
    ValidateRequest,
    ValidateResponse,
)
from ado2gh.api.credentials.credential_validation import (  # noqa: F401
    validate_ado_pat,  # re-exported: tests patch services.accelerator_api.main.validate_ado_pat
    validate_github_token,  # re-exported: same, .main.validate_github_token
)
from ado2gh.api.live_approval_store import (
    migrate_scope_id,
)
from ado2gh.api.migration_scan import (
    scan_with_credentials,  # noqa: F401 -- re-exported: tests patch .main.scan_with_credentials
)
from ado2gh.api.pipeline_runner import (
    PipelineRunStore,
)
from ado2gh.api.platform_rbac import (
    operator_requires_live_approval,
)
from ado2gh.api.profile_governance import (
    ProfileGovernanceError,
    assert_profile_active_for_run,
    onboarding_status_payload,
)
from ado2gh.api.settings_store import SettingsStore
from ado2gh.auth.models import PlatformRole
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.redis_queue import RedisJobQueue
from ado2gh.models import ExecutionMode
from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
from ado2gh.state.factory import create_state_db
from ado2gh.state.job_store import JobStoreFactory
from services.accelerator_api.routes.history_routes import router as history_router

try:
    from services.accelerator_api.auth_routes import SESSION_COOKIE
    from services.accelerator_api.auth_routes import router as auth_router
except ImportError:
    from auth_routes import SESSION_COOKIE
    from auth_routes import router as auth_router
from ado2gh.auth.service import AuthService, auth_enabled
from services.accelerator_api.routes._shared import (
    _accel,
    _config_path,
    _governance_http_error,
    _live_store,
    _platform_user,
    _settings,
)
from services.accelerator_api.routes.migrate_routes import router as migrate_features_router
from services.accelerator_api.routes.pipeline_routes import router as pipeline_router
from services.accelerator_api.routes.profile_routes import router as profile_router
from services.accelerator_api.routes.proxy_routes import router as proxy_router
from services.accelerator_api.routes.settings_routes import router as settings_router

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps these off the runtime path
    from collections.abc import Awaitable, Callable

    from fastapi import Response

app = FastAPI(title="ADO2GH Accelerator API", version="5.1.0")
app.include_router(history_router)
app.include_router(auth_router)
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
async def platform_auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Require a valid platform session on every ``/v1/`` request.

    Anything outside ``/v1/`` passes straight through, as do the exempt
    endpoints in ``_AUTH_EXEMPT`` — the probes plus the bootstrap, login and
    register routes, which by definition run before a session exists. Every
    other ``/v1/`` request must carry a session cookie that resolves to a live
    session; the resolved account is left on ``request.state.platform_user``
    for the handlers and the RBAC helpers to read.

    When platform authentication is switched off the middleware short-circuits
    and lets everything through. That is deliberate — it is the single-user
    local development mode — and it is why the live-execution guards in
    ``ado2gh.api.platform_rbac`` are written to hold without an identity
    (GAP-002, GAP-005). Do not add an identity check here to compensate.

    Args:
        request: Incoming request.
        call_next: The rest of the ASGI chain.

    Returns:
        The downstream response, or a 401 JSON response when the request
        carries no session cookie or the cookie no longer resolves to a live
        session.
    """
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
    """Reconcile the platform-managed LLM model record when the app starts."""
    from ado2gh.api.llm.platform_managed_model import sync_on_startup

    sync_on_startup()


def _onboarding_redirect() -> str | None:
    """Work out whether the console must send the caller through onboarding.

    Returns:
        The console path to redirect to when no migration profile has been set
        up yet, otherwise ``None``.
    """
    from ado2gh.api.profile_governance import needs_profile_setup

    profiles = _settings.load().migration_profiles
    if needs_profile_setup(profiles):
        return "/onboarding/profile"
    return None


@app.get("/v1/onboarding/status", response_model=OnboardingStatusResponse)
def onboarding_status(request: Request) -> OnboardingStatusResponse:
    """Tell the console where the signed-in user has to go next.

    Combines the configured migration profiles with the caller's role, so a
    user who is not allowed to create a profile is told to wait for one rather
    than sent to a form they cannot submit. A caller the middleware did not
    resolve is treated as an operator, which is the least-privileged answer.

    Args:
        request: Incoming request; the caller's role is read from the session
            resolved by the auth middleware.

    Returns:
        Whether profile setup is still outstanding, how many active profiles
        exist, the default profile identifier, how many profiles await
        approval, the caller's role, any blocking message, the console path to
        redirect to, and whether the caller may submit a profile themselves.
    """
    user = _platform_user(request)
    role = user.role if user else PlatformRole.OPERATOR
    profiles = _settings.load().migration_profiles
    return OnboardingStatusResponse(**onboarding_status_payload(profiles, role))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe: confirm the process is up and serving.

    Deliberately checks nothing else, so a degraded dependency never takes the
    container down. Use ``GET /ready`` to find out whether a migration could
    actually run.

    Returns:
        The static status string and the service version.
    """
    return HealthResponse()


@app.get("/ready")
def ready() -> dict[str, object]:
    """Readiness probe: report whether this instance could actually migrate.

    Probes the moving parts a migration needs — the configured storage backend
    and its database, the ``gh`` CLI and its ``gei`` and ``ado2gh`` extensions,
    ``git`` on the PATH, and Redis — then judges readiness against only the
    checks the *active migration strategy* requires, so a mirror deployment is
    not held back by a missing ``gh`` extension it will never call. Every probe
    is best-effort: a failure is recorded as a failed check rather than raised,
    so the endpoint always answers.

    Returns:
        ``ready`` — whether every check the active strategy requires passed;
        and ``checks``, the individual probe results, the resolved storage
        backend and migration strategy, plus a remediation hint for each tool
        that is missing.
    """
    import shutil
    import subprocess

    from ado2gh.models import DEFAULT_MIGRATION_STRATEGY
    from ado2gh.state.storage_config import StorageConfig

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
def discover(req: DiscoverRequest) -> DiscoverResponse:
    """Scan the Azure DevOps organisation and write a discovery report.

    Reads projects, repositories and pipelines and writes the wave
    configuration and inventory files under the requested output directory.
    Nothing is migrated. Azure DevOps credentials are taken from the active
    migration profile when one is set, and fall back to the process
    environment otherwise.

    Args:
        req: Path to the migration config and the directory to write into.

    Returns:
        The directory the discovery output landed in.
    """
    active = _settings.get_active_profile()
    ado_url = active.ado_org_url if active else None
    ado_pat = active.ado_pat if active else None
    result = _accel().discover(req, ado_url=ado_url, ado_pat=ado_pat)
    return DiscoverResponse(output_dir=result.output_dir)


@app.post("/v1/plan", response_model=PlanResponse, deprecated=True, tags=["deprecated"])
def plan(req: PlanRequest) -> PlanResponse:
    """Preview the waves a migration config resolves to, without running them.

    Deprecated: use ``POST /v1/migration/wave`` for wave creation and
    management. Nothing is migrated and no run is recorded; the state database
    is only opened so it exists for the later phases.

    Args:
        req: Path to the migration config, the optional wave to narrow to, and
            the state database path.

    Returns:
        One entry per matching wave with its identifier, name, repository
        count and pipeline count.
    """
    _, waves = ConfigLoader.load(req.config_path)
    create_state_db(req.db_path)
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
def migrate(req: RunWaveRequest, request: Request) -> list[RunWaveResponse]:
    """Run one migration wave, or every wave in the config.

    The request passes three gates before any work starts. The active
    migration profile must be approved and runnable; a caller who needs
    approval for live execution must already hold one, either quoted as
    ``live_approval_id`` or standing for this migrate scope; and the config
    must actually contain the requested wave. A live run without an approval
    creates a pending approval request and refuses the call, so the console can
    poll for the decision and retry. ``dry_run`` decides everything else: the
    default rehearses, while a live run pushes into GitHub for real.

    Args:
        req: Wave selection, config and database paths, the dry-run flag and
            an optional identifier of a previously granted live approval.
        request: Incoming request; the caller is read from the session the
            auth middleware resolved.

    Returns:
        A single-element list holding the wave's identifier, status and the
        completed, failed and total repository counts. The list shape is kept
        for callers that expect one entry per wave.

    Raises:
        HTTPException: 403 when the profile's governance state forbids the run,
            or ``awaiting_approval`` with the new approval's identifier when a
            live run needs one; 400 when the wave is unknown, the config is
            invalid, or the Azure DevOps or GitHub credentials are missing;
            500 for any other failure, with the underlying message.
    """
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
        if operator_requires_live_approval(
            user, ExecutionMode.from_dry_run(dry_run=req.dry_run),
        ):
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
                    LiveApprovalCreateRequest(
                        scope_type="migrate_job",
                        scope_id=scope_id,
                        profile_id=profile_id,
                        reason_request="Dashboard live migrate",
                        context=req.model_dump(exclude={"live_approval_id"}),
                    ),
                )
                raise HTTPException(
                    status_code=403,
                    detail={"code": "awaiting_approval", "approval_id": approval["id"]},
                )
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
        gh_token = active.github_tokens[0].token if active and active.github_tokens else None
        result = accel.run_wave(req, ado_url=ado_url, ado_pat=ado_pat, gh_token=gh_token)
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
def validate(req: ValidateRequest) -> ValidateResponse:
    """Verify migrated repositories against Azure DevOps at commit-SHA level.

    Compares the HEAD commit on each side rather than counting branches, so a
    pass is evidence the code actually transferred. The repository set comes
    from the first request field that yields one — inline text, an input file,
    a phase on the active profile, then the wave config.

    Args:
        req: Where to read the repository set from, which config and database
            to use, and where to write the CSV report.

    Returns:
        The number of repositories checked, how many matched, how many failed,
        and the normalised per-repository detail rows.

    Raises:
        HTTPException: 400 when an input path does not exist or the request
            resolves to no usable repository set.
    """
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


def _assess_pipeline_readiness(req: ReadinessRequest) -> ReadinessResponse:
    """Assess pipeline conversion effort, refreshing the inventory if needed.

    Shared body of the GET and POST readiness endpoints. Re-scans Azure DevOps
    when the caller asks for it or when the state database holds no inventory
    at all, then scores every inventoried pipeline against the latest known
    pipeline and repository migration outcomes.

    Args:
        req: Config and database paths, plus whether to force an inventory
            refresh; empty paths fall back to the stored advanced settings.

    Returns:
        Automatic, assisted and manual pipeline counts, the pipeline total and
        estimated effort in hours, whether the inventory was refreshed on this
        call, and the per-pipeline assessment rows.
    """
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
def pipeline_readiness(req: ReadinessRequest) -> ReadinessResponse:
    """Assess how much work converting the inventoried pipelines will take.

    Classifies every pipeline as automatic, assisted or manual and totals the
    estimated effort, so a team can size the pipeline work before committing to
    a migration. Identical to ``POST /v1/pipeline-readiness``; both are kept
    because the console and the CLI call different ones.

    Args:
        req: Config and database paths, plus whether to re-scan Azure DevOps
            first; empty paths fall back to the stored advanced settings.

    Returns:
        Automatic, assisted and manual pipeline counts, the pipeline total and
        estimated effort in hours, whether the inventory was refreshed, and
        the per-pipeline assessment rows.
    """
    return _assess_pipeline_readiness(req)


@app.post("/v1/pipeline-readiness", response_model=ReadinessResponse)
def pipeline_readiness_post(req: ReadinessRequest) -> ReadinessResponse:
    """Assess how much work converting the inventoried pipelines will take.

    The POST form of ``GET /v1/pipeline/readiness``, for clients that would
    rather not put a body on a GET. Same inputs, same answer.

    Args:
        req: Config and database paths, plus whether to re-scan Azure DevOps
            first; empty paths fall back to the stored advanced settings.

    Returns:
        Automatic, assisted and manual pipeline counts, the pipeline total and
        estimated effort in hours, whether the inventory was refreshed, and
        the per-pipeline assessment rows.
    """
    return _assess_pipeline_readiness(req)


@app.get("/v1/migration/status")
def migration_status() -> dict[str, object]:
    """Report per-repository migration progress for the status dashboard.

    Merges what the state database knows about each repository with the ten
    most recent pipeline runs, so a repository shows both its recorded
    migration outcome and how its last run went. The database path comes from
    the stored advanced settings.

    Returns:
        A summary of tracked, git-migrated, failed and partial repository
        counts alongside the per-repository rows and the recent run outcomes.
    """
    from ado2gh.api.migration_status_report import build_migration_status_report
    from ado2gh.api.pipeline_runner import PipelineRunStore

    adv = _settings.load().advanced
    db = create_state_db(adv.db_path)
    runs, _ = PipelineRunStore.list_runs(limit=10, offset=0)
    report = build_migration_status_report(db, pipeline_runs=runs)
    return report


@app.get("/v1/dashboard", response_model=DashboardSnapshot)
def dashboard(db_path: str = "migration_state.db") -> DashboardSnapshot:
    """Return the console's dashboard snapshot in a single call.

    Bundles the repository and pipeline totals, the inventory count, every
    phase gate and the runs currently in flight, so the dashboard renders
    without fanning out across several endpoints.

    Args:
        db_path: State database to read the counts and gates from.

    Returns:
        Total, completed and failed repository counts, the pipeline total and
        inventory count, the phase gates, and one entry per active migration
        carrying its status, phase, wave, current step and who started it.
    """
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
def freshness(req: FreshnessRequest) -> FreshnessResponse:
    """Check whether one migrated repository is still level with its source.

    Reads the HEAD commit of the repository's default branch on both sides and
    compares them, which is how a team spots commits pushed to Azure DevOps
    after the migration ran. A GitHub repository that cannot be read is
    reported as an empty SHA and therefore not fresh, rather than as an error.

    Args:
        req: The Azure DevOps project and repository to check, plus the config
            and database paths. Credentials and the GitHub organisation come
            from the active migration profile, or from the config file when no
            profile is set.

    Returns:
        The project and repository, both HEAD commit SHAs, and ``fresh``,
        which is true only when both were read and match.
    """
    global_cfg, _ = ConfigLoader.load(req.config_path)
    from ado2gh.api.accelerator import _build_ado_client, _build_gh_client

    active = _settings.get_active_profile()
    ado_url = active.ado_org_url if active else None
    ado_pat = active.ado_pat if active else None
    gh_org = active.gh_org if active else global_cfg.get("gh_org", "")
    gh_token = active.github_tokens[0].token if active and active.github_tokens else None
    ado = _build_ado_client(global_cfg, ado_url=ado_url, ado_pat=ado_pat)
    gh = _build_gh_client(global_cfg, gh_token=gh_token)
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
def enqueue_job(req: JobEnqueueRequest) -> JobStatusResponse:
    """Queue a background migration job and return its record.

    In lightweight mode there is no worker, so the job is completed inline and
    comes back already finished. Otherwise it is pushed onto the Redis queue
    for a worker to pick up; a queue that cannot be reached is not fatal, since
    the job is already durable in the job store and a worker will find it.

    Args:
        req: The job type, its payload, and an optional idempotency key that
            makes a retry return the job already queued instead of enqueuing a
            second one.

    Returns:
        ``job`` — the stored job record, carrying its identifier and status.
    """
    payload = req.payload or {}
    dry_run = payload.get("dry_run", True)
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
def job_status(job_id: str) -> JobStatusResponse:
    """Look up a queued background job by identifier.

    Args:
        job_id: Identifier returned when the job was enqueued.

    Returns:
        ``job`` — the stored job record, carrying its status and result.

    Raises:
        HTTPException: 404 when no job carries that identifier.
    """
    job = JobStoreFactory.from_env().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(job=job)
