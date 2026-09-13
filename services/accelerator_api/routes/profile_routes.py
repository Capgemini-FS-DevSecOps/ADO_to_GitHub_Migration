"""Profile management, scan, validation, token, and discovery route handlers."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    DeleteProfileRequest,
    DenyProfileRequest,
    DiscoveryRepoItem,
    DiscoveryResponse,
    MigrationProfileRequest,
    MigrationProfileResponse,
    MigrationScanRequest,
    MigrationScanResponse,
    PhaseAssignmentRequest,
    PhaseRecommendation,
    ProfileSetupRequest,
)
from ado2gh.api.platform_rbac import require_manage_settings
from ado2gh.api.profile_governance import (
    ProfileGovernanceError,
    assert_operator_can_submit,
    write_profile_audit,
)
from ado2gh.auth.models import PlatformRole
from services.accelerator_api.routes import _shared
from services.accelerator_api.routes._shared import (
    _platform_user,
    _require_active_profile,
    _require_admin,
    _require_profile,
    _settings,
)
from services.accelerator_api.routes.profile_credential_routes import router as _credential_router

router = APIRouter()


@router.get("/v1/settings/profiles/pending", response_model=list[MigrationProfileResponse])
def list_pending_profiles(request: Request) -> list[MigrationProfileResponse]:
    """List every migration profile that is waiting for administrator approval.

    Args:
        request: Incoming request, used to enforce the administrator role.

    Returns:
        Public views of all profiles whose status is ``pending_approval``.

    Raises:
        HTTPException: 401 or 403 when the caller is not an administrator.
    """
    _require_admin(request)
    pending = [
        p for p in _settings.load().migration_profiles
        if p.status == "pending_approval"
    ]
    return [MigrationProfileResponse(**p.to_public()) for p in pending]


@router.get("/v1/settings/profiles/mine/pending", response_model=list[MigrationProfileResponse])
def list_my_pending_profiles(request: Request) -> list[MigrationProfileResponse]:
    """List the calling user's own profile submissions that are pending or denied.

    Lets an operator track their submissions without administrator rights.

    Args:
        request: Incoming request, used to identify the signed-in user.

    Returns:
        Public views of the caller's profiles in ``pending_approval`` or ``denied``
        status; an empty list when they have submitted none.

    Raises:
        HTTPException: 401 when no authenticated user is attached to the request.
    """
    user = _platform_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    mine = [
        p for p in _settings.load().migration_profiles
        if p.submitted_by == user.username and p.status in ("pending_approval", "denied")
    ]
    return [MigrationProfileResponse(**p.to_public()) for p in mine]


@router.get("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def get_migration_profile(profile_id: str) -> MigrationProfileResponse:
    """Fetch a single migration profile by identifier.

    Args:
        profile_id: Identifier of the migration profile to fetch.

    Returns:
        The public view of the profile, with all secret material masked.

    Raises:
        HTTPException: 404 when no profile carries that identifier.
    """
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@router.post("/v1/settings/profiles", response_model=MigrationProfileResponse)
def create_profile(req: MigrationProfileRequest, request: Request) -> MigrationProfileResponse:
    """Create a migration profile, or overwrite one that already exists.

    Administrative counterpart to the guided setup endpoint: it stores the profile
    as supplied without running any credential validation or discovery scan.

    Args:
        req: Full profile definition to store.
        request: Incoming request, used to enforce the settings-management right.

    Returns:
        The public view of the stored profile, including its assigned identifier.

    Raises:
        HTTPException: 401 or 403 when the caller may not manage settings.
    """
    require_manage_settings(request)
    p = _settings.upsert_profile(req.model_dump())
    return MigrationProfileResponse(**p.to_public())


@router.post("/v1/settings/profiles/setup", response_model=MigrationProfileResponse)
def setup_profile(req: ProfileSetupRequest, request: Request) -> MigrationProfileResponse:
    """Onboard a migration profile through the guided setup flow.

    Both the Azure DevOps and the GitHub credentials in the request are checked
    against the live services before anything is stored, and each failed check is
    written to the profile audit log. Whether the new profile becomes active at
    once or is queued for approval depends on the caller's role: an administrator
    gets an active profile, an operator gets a submission. When the profile does
    become active it is applied to the process environment and an initial
    discovery scan runs; a scan failure is logged and does not fail the request.

    Args:
        req: Setup payload carrying the organisation URLs and the credentials to verify.
        request: Incoming request, used to identify the submitting user and their role.

    Returns:
        The public view of the created profile. Its ``status`` says whether it is
        active or awaiting approval.

    Raises:
        HTTPException: 403 when the caller's role is not permitted to submit a
            profile, or 400 when either credential check fails.
    """
    user = _platform_user(request)
    role = user.role.value if user else PlatformRole.ADMIN.value
    profiles = _settings.load().migration_profiles
    if user:
        try:
            assert_operator_can_submit(profiles, user.role)
        except ProfileGovernanceError as exc:
            raise HTTPException(status_code=403, detail=exc.code)

    ado_result = _shared.validate_ado_pat(req.ado_org_url, req.ado_pat)
    if not ado_result["valid"]:
        if user:
            write_profile_audit(
                "profile.validation_failed",
                profile_id="_pending",
                actor=user.username,
                payload={"step": "ado", "message": ado_result["message"]},
            )
        raise HTTPException(status_code=400, detail=ado_result["message"])
    gh_result = _shared.validate_github_token(req.github_token, req.gh_org)
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
            raw = _shared.scan_with_credentials(
                p.ado_org_url, p.ado_pat, gh_org=p.gh_org,
            )
            _shared.persist_scan_results(p.id, raw)
            _settings.record_scan_summary(p.id, raw)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Profile setup scan failed: %s", exc)
    return MigrationProfileResponse(**p.to_public())


def _scan_response(raw: dict) -> MigrationScanResponse:
    """Convert a raw scan result mapping into the wire response model.

    Every field is read defensively so that a partial or older scan record still
    produces a well-formed response rather than raising.

    Args:
        raw: Scan result mapping as produced or persisted by the scan helpers.

    Returns:
        The scan response model, with per-phase recommendation buckets rebuilt
        and missing keys replaced by empty defaults.
    """
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
        org_inventory=raw.get("org_inventory", {}),
        pipeline_inventory=raw.get("pipeline_inventory", {}),
        inventory_gaps=raw.get("inventory_gaps", []),
        warnings=raw.get("warnings", []),
        status=raw.get("status", "ok"),
    )


@router.post("/v1/migration/scan", response_model=MigrationScanResponse)
def migration_scan_inline(req: MigrationScanRequest) -> MigrationScanResponse:
    """Scan an Azure DevOps organisation with credentials supplied in the request.

    Used before any profile exists, so nothing is persisted: the credentials are
    verified, the scan runs inline, and the result is returned to the caller.

    Args:
        req: Scan request carrying the source organisation URL, its access
            credential, the target GitHub organisation and an optional cap on
            how many repositories to walk.

    Returns:
        The discovery scan result: counts, per-phase recommendations, project and
        inventory detail, and any warnings raised while scanning.

    Raises:
        HTTPException: 400 when the organisation URL or credential is missing or
            rejected by Azure DevOps, or 502 when the scan itself fails.
    """
    if not req.ado_org_url or not req.ado_pat:
        raise HTTPException(status_code=400, detail="ADO org URL and PAT are required")
    ado_result = _shared.validate_ado_pat(req.ado_org_url, req.ado_pat)
    if not ado_result["valid"]:
        raise HTTPException(status_code=400, detail=ado_result["message"])
    try:
        raw = _shared.scan_with_credentials(
            req.ado_org_url, req.ado_pat, gh_org=req.gh_org, max_repos=req.max_repos,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _scan_response(raw)


@router.post("/v1/settings/profiles/{profile_id}/scan")
def migration_scan_profile(
    profile_id: str,
    max_repos: int | None = None,
    sync: bool = False,  # noqa: FBT001,FBT002
) -> object:
    """Scan an existing profile's Azure DevOps organisation, inline or in the background.

    The profile must be active and must already hold source credentials; they are
    read from the profile rather than sent by the caller.

    Args:
        profile_id: Identifier of the migration profile to scan.
        max_repos: Optional cap on how many repositories to walk. Omit to scan all.
        sync: Selects the execution mode. When ``true`` the scan runs inline and
            the finished scan results are returned in this response, so the call
            blocks for as long as the scan takes. When ``false`` (the default) a
            background scan job is started and the job record is returned
            immediately; poll the profile scan status endpoint for progress and
            read the results from the profile scan endpoint once it completes.

    Returns:
        With ``sync=true``, the full discovery scan result. With ``sync=false``,
        the background job record describing the scan that was queued.

    Raises:
        HTTPException: 400 when the profile holds no source credentials or the
            background job could not be started, 502 when an inline scan fails,
            or the governance status when the profile is not active.
    """
    p = _require_active_profile(profile_id)
    if not p.ado_org_url or not p.ado_pat:
        raise HTTPException(status_code=400, detail="Profile missing ADO credentials")
    if sync:
        try:
            raw = _settings._execute_profile_scan(profile_id, max_repos=max_repos)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _scan_response(raw)
    job = _settings.start_profile_scan(profile_id, max_repos=max_repos)
    if job.get("status") == "error":
        raise HTTPException(status_code=400, detail=job.get("error", "Scan failed to start"))
    return job


@router.get("/v1/settings/profiles/{profile_id}/scan/status")
def profile_scan_status(profile_id: str) -> dict[str, object]:
    """Report the progress of the background scan for a profile.

    Poll this after starting an asynchronous scan to learn when results are ready.

    Args:
        profile_id: Identifier of the migration profile being scanned.

    Returns:
        The rescan status record: the job state plus whatever progress counters
        and timestamps the scan has published so far.

    Raises:
        HTTPException: 404 when no profile carries that identifier.
    """
    _require_profile(profile_id)
    return _settings.profile_rescan_status(profile_id)


@router.get("/v1/settings/profiles/{profile_id}/scan")
def get_profile_scan(profile_id: str) -> MigrationScanResponse:
    """Return the most recently stored discovery scan for a profile.

    Reads the cached scan; it never starts a new one.

    Args:
        profile_id: Identifier of the migration profile whose scan to read.

    Returns:
        The stored discovery scan result: counts, per-phase recommendations,
        project and inventory detail, and any warnings from that scan.

    Raises:
        HTTPException: 404 when no profile carries that identifier, or when the
            profile has never been scanned.
    """
    _require_profile(profile_id)
    data = _shared.load_scan_results(profile_id)
    if not data:
        raise HTTPException(status_code=404, detail="No scan results for this profile")
    return _scan_response(data)


@router.put("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def update_profile(profile_id: str, req: MigrationProfileRequest, request: Request) -> MigrationProfileResponse:
    """Replace the stored definition of an existing migration profile.

    Args:
        profile_id: Identifier of the migration profile to update.
        req: Full replacement definition for the profile.
        request: Incoming request, used to enforce the settings-management right.

    Returns:
        The public view of the updated profile.

    Raises:
        HTTPException: 401 or 403 when the caller may not manage settings, or 404
            when no profile carries that identifier.
    """
    require_manage_settings(request)
    try:
        p = _settings.upsert_profile(req.model_dump(), profile_id=profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@router.delete("/v1/settings/profiles/{profile_id}")
def delete_profile(
    profile_id: str, request: Request, body: DeleteProfileRequest | None = None,
) -> dict[str, object]:
    """Permanently delete a migration profile and record the deletion in the audit log.

    Deleting the profile that is currently the default requires naming its
    replacement in the body, and the last remaining active profile cannot be
    deleted at all.

    Args:
        profile_id: Identifier of the migration profile to delete.
        request: Incoming request, used to enforce the administrator role and
            attribute the audit entry.
        body: Optional payload naming the profile that should become the new
            default. Required only when deleting the current default.

    Returns:
        A confirmation mapping whose ``deleted`` key echoes the deleted profile's
        identifier.

    Raises:
        HTTPException: 401 or 403 when the caller is not an administrator, 409
            when this is the last active profile, or 400 when a replacement
            default is missing or does not name a usable profile.
    """
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


@router.post("/v1/settings/profiles/{profile_id}/set-default")
def set_profile_default(profile_id: str, request: Request) -> MigrationProfileResponse:
    """Make a profile the platform default and record the change in the audit log.

    Args:
        profile_id: Identifier of the migration profile to promote.
        request: Incoming request, used to enforce the administrator role and
            attribute the audit entry.

    Returns:
        The public view of the profile that is now the default.

    Raises:
        HTTPException: 401 or 403 when the caller is not an administrator, 404
            when no profile carries that identifier, or 403 when the profile is
            not in a state that may be made default.
    """
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


@router.post("/v1/settings/profiles/{profile_id}/deactivate", response_model=MigrationProfileResponse)
def deactivate_profile(
    profile_id: str, request: Request, body: DeleteProfileRequest | None = None,
) -> MigrationProfileResponse:
    """Retire a migration profile without deleting it, keeping its history intact.

    Deactivating the current default requires naming its replacement in the body,
    and the last remaining active profile cannot be deactivated.

    Args:
        profile_id: Identifier of the migration profile to deactivate.
        request: Incoming request, used to enforce the administrator role and
            attribute the audit entry.
        body: Optional payload naming the profile that should become the new
            default. Required only when deactivating the current default.

    Returns:
        The public view of the profile in its now-inactive state.

    Raises:
        HTTPException: 401 or 403 when the caller is not an administrator, 404
            when no profile carries that identifier, 409 when this is the last
            active profile, or 400 for any other rejected deactivation.
    """
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


@router.post("/v1/settings/profiles/{profile_id}/approve", response_model=MigrationProfileResponse)
def approve_profile(profile_id: str, request: Request) -> MigrationProfileResponse:
    """Approve a submitted profile after re-checking its stored credentials.

    The profile's Azure DevOps credential, and its first GitHub token when it has
    one, are verified against the live services first, so a submission whose
    credentials have expired since it was raised is rejected rather than approved.

    Args:
        profile_id: Identifier of the migration profile to approve.
        request: Incoming request, used to enforce the administrator role and
            attribute the audit entry.

    Returns:
        The public view of the approved, now-active profile.

    Raises:
        HTTPException: 401 or 403 when the caller is not an administrator, 404
            when no profile carries that identifier, or 400 when a stored
            credential fails validation or the profile is not awaiting approval.
    """
    _require_admin(request)
    p = _require_profile(profile_id)
    ado_result = _shared.validate_ado_pat(p.ado_org_url, p.ado_pat)
    if not ado_result["valid"]:
        raise HTTPException(status_code=400, detail=ado_result["message"])
    if p.github_tokens:
        gh_result = _shared.validate_github_token(p.github_tokens[0].token, p.gh_org)
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


@router.post("/v1/settings/profiles/{profile_id}/deny", response_model=MigrationProfileResponse)
def deny_profile(
    profile_id: str, request: Request, body: DenyProfileRequest | None = None,
) -> MigrationProfileResponse:
    """Deny a submitted profile, recording the stated reason in the audit log.

    The submitter can appeal a denied profile through the appeal endpoint.

    Args:
        profile_id: Identifier of the migration profile to deny.
        request: Incoming request, used to enforce the administrator role and
            attribute the audit entry.
        body: Optional payload carrying the reason shown to the submitter. When
            omitted the profile is denied with an empty reason.

    Returns:
        The public view of the profile in its now-denied state.

    Raises:
        HTTPException: 401 or 403 when the caller is not an administrator, 404
            when no profile carries that identifier, or 400 when the profile is
            not awaiting approval.
    """
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


@router.post("/v1/settings/profiles/{profile_id}/appeal", response_model=MigrationProfileResponse)
def appeal_profile(profile_id: str, request: Request) -> MigrationProfileResponse:
    """Return a denied profile to the approval queue on its submitter's request.

    Only the user who originally submitted the profile may appeal it.

    Args:
        profile_id: Identifier of the denied migration profile to appeal.
        request: Incoming request, used to identify the signed-in user.

    Returns:
        The public view of the profile, back in ``pending_approval`` status.

    Raises:
        HTTPException: 401 when no authenticated user is attached to the request,
            403 when the caller did not submit this profile, 404 when no profile
            carries that identifier, or 400 when the profile is not denied.
    """
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


@router.post("/v1/settings/profiles/{profile_id}/activate")
def activate_profile(profile_id: str) -> dict[str, object]:
    """Select a profile as the active one and apply its settings to the process.

    Subsequent operations that do not name a profile use this one, and its
    organisation URLs and credentials are pushed into the process environment.

    Args:
        profile_id: Identifier of the migration profile to activate.

    Returns:
        A confirmation mapping whose ``active_profile_id`` key echoes the
        activated profile's identifier.

    Raises:
        HTTPException: 404 when no profile carries that identifier.
    """
    try:
        _settings.set_active(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    _settings.apply_to_process_env()
    return {"active_profile_id": profile_id}


@router.get("/v1/settings/profiles/{profile_id}/discovery", response_model=DiscoveryResponse)
def get_profile_discovery(profile_id: str) -> DiscoveryResponse:
    """Return the discovery view of a profile's scan, repository by repository.

    The per-repository rows come from the state database where the scan has been
    persisted with its phase assignments; when that table holds nothing for this
    profile the rows are rebuilt from the cached scan's recommendation buckets
    instead. A profile that has never been scanned yields an empty view with
    status ``empty`` rather than an error.

    Args:
        profile_id: Identifier of the migration profile whose discovery to read.

    Returns:
        The discovery view: the flat repository list with risk scores and phase
        assignments, plus the scan's counts, recommendations, project and
        inventory detail, gaps and warnings.

    Raises:
        HTTPException: 404 when no profile carries that identifier, or the
            governance status when the profile is not active.
    """
    _require_active_profile(profile_id)
    data = _shared.load_scan_results(profile_id)
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    repos: list[DiscoveryRepoItem] = []
    if data and hasattr(db, "get_profile_scan_repos"):
        for r in db.get_profile_scan_repos(profile_id):
            repos.append(DiscoveryRepoItem(
                project=r["project"],
                repo_name=r["repo_name"],
                total_score=r["total_score"],
                suggested_phase=None,
                assigned_phase=None,
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
                    suggested_phase=None,
                    assigned_phase=None,
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
        org_inventory=data.get("org_inventory", {}) if data else {},
        pipeline_inventory_count=(
            (data.get("org_inventory") or {}).get("pipeline_inventory_count")
            or (data.get("pipeline_inventory") or {}).get("inventory_count")
            or (db.inventory_count() if hasattr(db, "inventory_count") else 0)
        ),
        inventory_gaps=data.get("inventory_gaps", []) if data else [],
        warnings=data.get("warnings", []) if data else [],
        status=data.get("status", "ok") if data else "empty",
    )


@router.put("/v1/settings/profiles/{profile_id}/phase-assignments")
def update_phase_assignments(
    profile_id: str, req: PhaseAssignmentRequest, request: Request,
) -> dict[str, object]:
    """Move scanned repositories between migration phases and re-sync their risk scores.

    If the state database holds no scan rows for this profile yet, the cached scan
    is written to it first and the assignments are then applied, so the console can
    save phases straight after a scan. Applying the assignments also syncs them to
    the risk-score table that the phase runner reads, and refreshes the cached scan.

    Args:
        profile_id: Identifier of the migration profile owning the repositories.
        req: Assignment payload listing each repository and its target phase.
        request: Incoming request, used to enforce the settings-management right.

    Returns:
        A mapping with ``updated`` (repositories moved), ``synced_risk_scores``
        (risk-score rows written), ``scan`` (the refreshed scan results, or null
        when none are cached) and a ``message`` telling the operator to rebuild
        the plan or start a new run to pick the changes up.

    Raises:
        HTTPException: 401 or 403 when the caller may not manage settings, 404
            when no profile carries that identifier or none of the named
            repositories matched, or 501 when the configured storage backend does
            not support phase assignment.
    """
    require_manage_settings(request)
    _require_profile(profile_id)
    from ado2gh.api.state_db import get_state_db

    db = get_state_db()
    if not hasattr(db, "update_profile_repo_phases"):
        raise HTTPException(status_code=501, detail="Phase assignment not supported on this storage backend")
    updates = [a.model_dump() for a in req.assignments]
    count = db.update_profile_repo_phases(profile_id, updates)
    if count == 0 and updates and hasattr(db, "save_profile_scan"):
        if not db.get_profile_scan_repos(profile_id):
            cached = _shared.load_scan_results(profile_id)
            if cached and cached.get("recommendations"):
                db.save_profile_scan(profile_id, cached)
                count = db.update_profile_repo_phases(profile_id, updates)
    if count == 0 and updates:
        raise HTTPException(status_code=404, detail="No matching repos found to update")
    from ado2gh.api.profile_discovery import sync_profile_scan_to_risk_scores

    adv = _settings.load().advanced
    synced = sync_profile_scan_to_risk_scores(profile_id, config_path=adv.config_path)
    refreshed = _shared.load_scan_results(profile_id)
    if refreshed:
        _shared.persist_scan_results(profile_id, refreshed)
    return {
        "updated": count,
        "synced_risk_scores": synced,
        "scan": refreshed,
        "message": (
            "Phase assignments saved. Rebuild the agent migration plan or start a new "
            "pipeline run to pick up the updated repo list."
        ),
    }


# Mounted last so this module's own routes keep the registration order they were
# written and tested in; it also keeps main.py's single
# include_router(profile_routes.router) call correct.
router.include_router(_credential_router)
