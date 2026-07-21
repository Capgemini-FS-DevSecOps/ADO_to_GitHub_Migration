"""Profile management, scan, validation, token, and discovery route handlers."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    DeleteProfileRequest,
    DenyProfileRequest,
    DiscoveryResponse,
    DiscoveryRepoItem,
    GitHubTokenRequest,
    GitHubTokenResponse,
    MigrationProfileRequest,
    MigrationProfileResponse,
    MigrationScanRequest,
    MigrationScanResponse,
    MigrationProfileResponse,
    PhaseAssignmentRequest,
    PhaseRecommendation,
    ProfileSetupRequest,
    ValidateAdoPatRequest,
    ValidateAdoPatResponse,
    ValidateConnectionResponse,
    ValidateGitHubTokenRequest,
    ValidateGitHubTokenResponse,
)
from ado2gh.api.platform_rbac import require_manage_settings
from ado2gh.api.profile_governance import (
    assert_operator_can_submit,
    ProfileGovernanceError,
    write_profile_audit,
)
from ado2gh.auth.models import PlatformRole

from services.accelerator_api.routes import _shared
from services.accelerator_api.routes._shared import (
    _platform_user,
    _require_admin,
    _require_profile,
    _settings,
)

router = APIRouter()


@router.get("/v1/settings/profiles/pending", response_model=list[MigrationProfileResponse])
def list_pending_profiles(request: Request):
    _require_admin(request)
    pending = [
        p for p in _settings.load().migration_profiles
        if p.status == "pending_approval"
    ]
    return [MigrationProfileResponse(**p.to_public()) for p in pending]


@router.get("/v1/settings/profiles/mine/pending", response_model=list[MigrationProfileResponse])
def list_my_pending_profiles(request: Request):
    user = _platform_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    mine = [
        p for p in _settings.load().migration_profiles
        if p.submitted_by == user.username and p.status in ("pending_approval", "denied")
    ]
    return [MigrationProfileResponse(**p.to_public()) for p in mine]


@router.get("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def get_migration_profile(profile_id: str):
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@router.post("/v1/settings/profiles", response_model=MigrationProfileResponse)
def create_profile(req: MigrationProfileRequest, request: Request):
    require_manage_settings(request)
    p = _settings.upsert_profile(req.model_dump())
    return MigrationProfileResponse(**p.to_public())


@router.post("/v1/settings/profiles/setup", response_model=MigrationProfileResponse)
def setup_profile(req: ProfileSetupRequest, request: Request):
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
def migration_scan_inline(req: MigrationScanRequest):
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
    sync: bool = False,
):
    p = _require_profile(profile_id, require_active=True)
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
def profile_scan_status(profile_id: str):
    _require_profile(profile_id)
    return _settings.profile_rescan_status(profile_id)


@router.get("/v1/settings/profiles/{profile_id}/scan")
def get_profile_scan(profile_id: str):
    _require_profile(profile_id)
    data = _shared.load_scan_results(profile_id)
    if not data:
        raise HTTPException(status_code=404, detail="No scan results for this profile")
    return _scan_response(data)


@router.put("/v1/settings/profiles/{profile_id}", response_model=MigrationProfileResponse)
def update_profile(profile_id: str, req: MigrationProfileRequest, request: Request):
    require_manage_settings(request)
    try:
        p = _settings.upsert_profile(req.model_dump(), profile_id=profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return MigrationProfileResponse(**p.to_public())


@router.delete("/v1/settings/profiles/{profile_id}")
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


@router.post("/v1/settings/profiles/{profile_id}/set-default")
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


@router.post("/v1/settings/profiles/{profile_id}/deactivate", response_model=MigrationProfileResponse)
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


@router.post("/v1/settings/profiles/{profile_id}/approve", response_model=MigrationProfileResponse)
def approve_profile(profile_id: str, request: Request):
    _require_admin(request)
    p = _require_profile(profile_id, require_active=False)
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


@router.post("/v1/settings/profiles/{profile_id}/appeal", response_model=MigrationProfileResponse)
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


@router.post("/v1/settings/profiles/{profile_id}/activate")
def activate_profile(profile_id: str):
    try:
        _settings.set_active(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    _settings.apply_to_process_env()
    return {"active_profile_id": profile_id}


@router.post("/v1/settings/profiles/{profile_id}/validate/source", response_model=ValidateAdoPatResponse)
def validate_profile_source(profile_id: str):
    p = _require_profile(profile_id)
    result = _shared.validate_ado_pat(p.ado_org_url, p.ado_pat)
    return ValidateAdoPatResponse(**result)


@router.post("/v1/settings/profiles/{profile_id}/validate", response_model=ValidateConnectionResponse)
def validate_migration_profile(profile_id: str):
    p = _require_profile(profile_id)
    ado_result = _shared.validate_ado_pat(p.ado_org_url, p.ado_pat)
    if not ado_result["valid"]:
        return ValidateConnectionResponse(valid=False, message=ado_result["message"])

    if not p.github_tokens:
        return ValidateConnectionResponse(
            valid=False,
            message="Source ADO OK — add GitHub tokens for this migration profile",
            ado_projects=ado_result["ado_projects"],
        )

    gh_result = _shared.validate_github_token(p.github_tokens[0].token, p.gh_org)
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


@router.post("/v1/settings/validate", response_model=ValidateConnectionResponse)
def validate_connection():
    profile = _settings.get_active_profile()
    if not profile:
        return ValidateConnectionResponse(valid=False, message="No active migration profile")
    return validate_migration_profile(profile.id)


@router.post("/v1/settings/profiles/{profile_id}/validate/ado", response_model=ValidateAdoPatResponse)
def validate_ado_for_profile(profile_id: str, req: ValidateAdoPatRequest):
    p = _require_profile(profile_id)
    ado_org = req.ado_org_url or p.ado_org_url
    ado_pat = req.ado_pat if req.ado_pat and req.ado_pat != "***" else p.ado_pat
    result = _shared.validate_ado_pat(ado_org, ado_pat)
    return ValidateAdoPatResponse(**result)


@router.get("/v1/settings/profiles/{profile_id}/tokens", response_model=list[GitHubTokenResponse])
def list_github_tokens(profile_id: str):
    p = _require_profile(profile_id)
    return [GitHubTokenResponse(**t.to_public()) for t in p.github_tokens]


@router.post("/v1/settings/profiles/{profile_id}/tokens", response_model=GitHubTokenResponse)
def create_github_token(profile_id: str, req: GitHubTokenRequest, request: Request):
    require_manage_settings(request)
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return GitHubTokenResponse(**t.to_public())


@router.put("/v1/settings/profiles/{profile_id}/tokens/{token_id}", response_model=GitHubTokenResponse)
def update_github_token(profile_id: str, token_id: str, req: GitHubTokenRequest, request: Request):
    require_manage_settings(request)
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump(), token_id=token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return GitHubTokenResponse(**t.to_public())


@router.delete("/v1/settings/profiles/{profile_id}/tokens/{token_id}")
def delete_github_token(profile_id: str, token_id: str, request: Request):
    require_manage_settings(request)
    try:
        _settings.delete_github_token(profile_id, token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return {"deleted": token_id}


@router.post("/v1/settings/profiles/{profile_id}/tokens/validate", response_model=ValidateGitHubTokenResponse)
def validate_github_token_inline(profile_id: str, req: ValidateGitHubTokenRequest):
    p = _require_profile(profile_id)
    result = _shared.validate_github_token(req.token, p.gh_org)
    return ValidateGitHubTokenResponse(**result)


@router.post("/v1/settings/profiles/{profile_id}/tokens/{token_id}/validate", response_model=ValidateGitHubTokenResponse)
def validate_saved_github_token(profile_id: str, token_id: str):
    p = _require_profile(profile_id)
    tok = _settings.get_github_token(profile_id, token_id)
    if not tok:
        raise HTTPException(status_code=404, detail="Token not found")
    result = _shared.validate_github_token(tok.token, p.gh_org)
    _settings.record_token_validation(profile_id, token_id, result)
    return ValidateGitHubTokenResponse(**result)


@router.post("/v1/settings/validate/ado", response_model=ValidateAdoPatResponse)
def validate_ado_inline(req: ValidateAdoPatRequest):
    result = _shared.validate_ado_pat(req.ado_org_url, req.ado_pat)
    return ValidateAdoPatResponse(**result)


@router.post("/v1/settings/validate/github", response_model=ValidateGitHubTokenResponse)
def validate_github_inline(req: ValidateGitHubTokenRequest):
    result = _shared.validate_github_token(req.token, req.gh_org)
    return ValidateGitHubTokenResponse(**result)


@router.get("/v1/settings/profiles/{profile_id}/discovery", response_model=DiscoveryResponse)
def get_profile_discovery(profile_id: str):
    _require_profile(profile_id, require_active=True)
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
