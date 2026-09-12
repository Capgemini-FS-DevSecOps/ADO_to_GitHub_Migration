"""Credential-validation and GitHub token routes for a migration profile.

These endpoints check Azure DevOps and GitHub credentials -- against a stored
profile, or standalone during setup wizard before a profile exists -- and manage
the GitHub tokens registered against a profile. They live apart from the rest of
the profile routes to keep each module under the per-file size limit; their
router is included back into ``profile_routes.router``, so the application still
registers a single profile router and every path is unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    GitHubTokenRequest,
    GitHubTokenResponse,
    ValidateAdoPatRequest,
    ValidateAdoPatResponse,
    ValidateConnectionResponse,
    ValidateGitHubTokenRequest,
    ValidateGitHubTokenResponse,
)
from ado2gh.api.platform_rbac import require_manage_settings
from services.accelerator_api.routes import _shared
from services.accelerator_api.routes._shared import _require_profile, _settings

router = APIRouter()


@router.post("/v1/settings/profiles/{profile_id}/validate/source", response_model=ValidateAdoPatResponse)
def validate_profile_source(profile_id: str) -> ValidateAdoPatResponse:
    """Check a profile's stored Azure DevOps credential against the live service.

    Args:
        profile_id: Identifier of the migration profile to check.

    Returns:
        The validation outcome: whether the credential works, a message safe to
        show an operator, and the number of projects it can reach.

    Raises:
        HTTPException: 404 when no profile carries that identifier. A credential
            that is simply invalid is reported in the response, not raised.
    """
    p = _require_profile(profile_id)
    result = _shared.validate_ado_pat(p.ado_org_url, p.ado_pat)
    return ValidateAdoPatResponse(**result)


@router.post("/v1/settings/profiles/{profile_id}/validate", response_model=ValidateConnectionResponse)
def validate_migration_profile(profile_id: str) -> ValidateConnectionResponse:
    """Check both ends of a profile's connection, source first and then target.

    The Azure DevOps side is checked first and a failure there stops the check. A
    profile with a working source but no GitHub tokens is reported as incomplete
    rather than broken.

    Args:
        profile_id: Identifier of the migration profile to check.

    Returns:
        The combined outcome: overall validity, an operator-facing message naming
        which side failed, the reachable Azure DevOps project count and, when both
        sides work, the target token's remaining rate-limit budget.

    Raises:
        HTTPException: 404 when no profile carries that identifier. Credential
            failures are reported in the response, not raised.
    """
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
def validate_connection() -> ValidateConnectionResponse:
    """Check both ends of the connection for whichever profile is currently active.

    Convenience wrapper for callers that do not track a profile identifier.

    Returns:
        The same combined outcome as validating that profile by identifier, or an
        invalid result explaining that no profile is active.
    """
    profile = _settings.get_active_profile()
    if not profile:
        return ValidateConnectionResponse(valid=False, message="No active migration profile")
    return validate_migration_profile(profile.id)


@router.post("/v1/settings/profiles/{profile_id}/validate/ado", response_model=ValidateAdoPatResponse)
def validate_ado_for_profile(profile_id: str, req: ValidateAdoPatRequest) -> ValidateAdoPatResponse:
    """Check candidate Azure DevOps credentials against a profile before saving them.

    Either field in the request may be left out, in which case the profile's
    stored value is used instead; sending back the masked placeholder that the
    console displays counts as leaving the credential out. This lets the console
    re-check a profile whose credential the user has not retyped.

    Args:
        profile_id: Identifier of the migration profile supplying the fallbacks.
        req: Candidate organisation URL and credential to test.

    Returns:
        The validation outcome: whether the credential works, a message safe to
        show an operator, and the number of projects it can reach.

    Raises:
        HTTPException: 404 when no profile carries that identifier. A credential
            that is simply invalid is reported in the response, not raised.
    """
    p = _require_profile(profile_id)
    ado_org = req.ado_org_url or p.ado_org_url
    ado_pat = req.ado_pat if req.ado_pat and req.ado_pat != "***" else p.ado_pat
    result = _shared.validate_ado_pat(ado_org, ado_pat)
    return ValidateAdoPatResponse(**result)


@router.get("/v1/settings/profiles/{profile_id}/tokens", response_model=list[GitHubTokenResponse])
def list_github_tokens(profile_id: str) -> list[GitHubTokenResponse]:
    """List the GitHub tokens registered against a profile.

    Args:
        profile_id: Identifier of the migration profile whose tokens to list.

    Returns:
        The public view of each registered token — identifier, label and last
        known rate-limit state, never the secret value itself.

    Raises:
        HTTPException: 404 when no profile carries that identifier.
    """
    p = _require_profile(profile_id)
    return [GitHubTokenResponse(**t.to_public()) for t in p.github_tokens]


@router.post("/v1/settings/profiles/{profile_id}/tokens", response_model=GitHubTokenResponse)
def create_github_token(profile_id: str, req: GitHubTokenRequest, request: Request) -> GitHubTokenResponse:
    """Register an additional GitHub token against a profile.

    Profiles hold several tokens so that migration work can be spread across them
    to stay inside GitHub's rate limits.

    Args:
        profile_id: Identifier of the migration profile to add the token to.
        req: Token payload carrying its label and secret value.
        request: Incoming request, used to enforce the settings-management right.

    Returns:
        The public view of the stored token, with its assigned identifier and the
        secret value masked.

    Raises:
        HTTPException: 401 or 403 when the caller may not manage settings, or 404
            when no profile carries that identifier.
    """
    require_manage_settings(request)
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return GitHubTokenResponse(**t.to_public())


@router.put("/v1/settings/profiles/{profile_id}/tokens/{token_id}", response_model=GitHubTokenResponse)
def update_github_token(
    profile_id: str, token_id: str, req: GitHubTokenRequest, request: Request,
) -> GitHubTokenResponse:
    """Replace a GitHub token registered against a profile.

    Args:
        profile_id: Identifier of the migration profile owning the token.
        token_id: Identifier of the token to replace.
        req: Replacement token payload carrying its label and secret value.
        request: Incoming request, used to enforce the settings-management right.

    Returns:
        The public view of the updated token, with the secret value masked.

    Raises:
        HTTPException: 401 or 403 when the caller may not manage settings, or 404
            when the profile or the token does not exist.
    """
    require_manage_settings(request)
    try:
        t = _settings.upsert_github_token(profile_id, req.model_dump(), token_id=token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return GitHubTokenResponse(**t.to_public())


@router.delete("/v1/settings/profiles/{profile_id}/tokens/{token_id}")
def delete_github_token(profile_id: str, token_id: str, request: Request) -> dict[str, object]:
    """Remove a GitHub token from a profile.

    Args:
        profile_id: Identifier of the migration profile owning the token.
        token_id: Identifier of the token to remove.
        request: Incoming request, used to enforce the settings-management right.

    Returns:
        A confirmation mapping whose ``deleted`` key echoes the removed token's
        identifier.

    Raises:
        HTTPException: 401 or 403 when the caller may not manage settings, or 404
            when the profile or the token does not exist.
    """
    require_manage_settings(request)
    try:
        _settings.delete_github_token(profile_id, token_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Token or profile not found")
    return {"deleted": token_id}


@router.post("/v1/settings/profiles/{profile_id}/tokens/validate", response_model=ValidateGitHubTokenResponse)
def validate_github_token_inline(
    profile_id: str, req: ValidateGitHubTokenRequest,
) -> ValidateGitHubTokenResponse:
    """Check a candidate GitHub token against a profile's target organisation.

    Nothing is stored, so the console can test a token before the operator commits
    to saving it. The organisation comes from the profile, not the request.

    Args:
        profile_id: Identifier of the migration profile supplying the target
            organisation.
        req: Candidate token payload to test.

    Returns:
        The validation outcome: whether the token works, an operator-facing
        message, the authenticated login and the remaining rate-limit budget.

    Raises:
        HTTPException: 404 when no profile carries that identifier. A token that
            is simply invalid is reported in the response, not raised.
    """
    p = _require_profile(profile_id)
    result = _shared.validate_github_token(req.token, p.gh_org)
    return ValidateGitHubTokenResponse(**result)


@router.post("/v1/settings/profiles/{profile_id}/tokens/{token_id}/validate", response_model=ValidateGitHubTokenResponse)
def validate_saved_github_token(profile_id: str, token_id: str) -> ValidateGitHubTokenResponse:
    """Re-check a stored GitHub token and record the outcome against the profile.

    Unlike the inline check this persists the result, so the token list reflects
    the freshly measured rate-limit budget.

    Args:
        profile_id: Identifier of the migration profile owning the token.
        token_id: Identifier of the stored token to re-check.

    Returns:
        The validation outcome: whether the token works, an operator-facing
        message, the authenticated login and the remaining rate-limit budget.

    Raises:
        HTTPException: 404 when the profile or the token does not exist. A token
            that is simply invalid is reported in the response, not raised.
    """
    p = _require_profile(profile_id)
    tok = _settings.get_github_token(profile_id, token_id)
    if not tok:
        raise HTTPException(status_code=404, detail="Token not found")
    result = _shared.validate_github_token(tok.token, p.gh_org)
    _settings.record_token_validation(profile_id, token_id, result)
    return ValidateGitHubTokenResponse(**result)


@router.post("/v1/settings/validate/ado", response_model=ValidateAdoPatResponse)
def validate_ado_inline(req: ValidateAdoPatRequest) -> ValidateAdoPatResponse:
    """Check Azure DevOps credentials that are not tied to any stored profile.

    Used by the setup wizard before a profile exists. Nothing is stored.

    Args:
        req: Organisation URL and credential to test.

    Returns:
        The validation outcome: whether the credential works, a message safe to
        show an operator, and the number of projects it can reach.
    """
    result = _shared.validate_ado_pat(req.ado_org_url, req.ado_pat)
    return ValidateAdoPatResponse(**result)


@router.post("/v1/settings/validate/github", response_model=ValidateGitHubTokenResponse)
def validate_github_inline(req: ValidateGitHubTokenRequest) -> ValidateGitHubTokenResponse:
    """Check a GitHub token and organisation that are not tied to any stored profile.

    Used by the setup wizard before a profile exists. Nothing is stored.

    Args:
        req: Token payload and target organisation to test.

    Returns:
        The validation outcome: whether the token works, an operator-facing
        message, the authenticated login and the remaining rate-limit budget.
    """
    result = _shared.validate_github_token(req.token, req.gh_org)
    return ValidateGitHubTokenResponse(**result)
