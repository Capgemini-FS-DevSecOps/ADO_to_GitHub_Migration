"""Settings, LLM, connectivity, and cloud credential route handlers.

Almost every endpoint here requires ``can_manage_models``; only the model
listing is open to other roles. Secrets are one-way — an API key, proxy password
or CA bundle goes in through a write and is only ever read back as a mask, never
as its value (CA-003).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.contracts import (
    AdvancedSettingsRequest,
    PhasesUpdateRequest,
    SettingsResponse,
)
from ado2gh.api.platform_rbac import require_manage_models
from ado2gh.api.profile_governance import write_profile_audit
from ado2gh.auth.models import PlatformRole
from services.accelerator_api.routes._shared import (
    _cloud_credentials,
    _connectivity,
    _llm_models,
    _maybe_audit_model_enabled,
    _platform_user,
    _settings,
    advanced_settings_as_dict,
)

router = APIRouter()


@router.get("/v1/settings/connectivity")
def get_connectivity(request: Request) -> dict[str, object]:
    """Return how the platform reaches the internet: proxy, TLS and model policy.

    Args:
        request: The incoming request, used for the capability check.

    Returns:
        The connectivity profile with its secrets withheld — the proxy toggle,
        host, port and username, a flag saying whether a custom CA bundle is
        configured, the custom-model-id toggle, and who last changed it and
        when. The proxy password comes back as a fixed mask when one is stored
        and as an empty string otherwise; the CA bundle itself is never
        returned.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    return _connectivity.load().to_public()


@router.put("/v1/settings/connectivity")
def put_connectivity(request: Request, body: dict) -> dict[str, object]:
    """Update the platform's outbound connectivity profile.

    Only the keys present in the body are applied, so a partial update leaves
    everything else alone. The two secret fields — the proxy password and the
    custom CA bundle — are write-only: sending back the mask that the ``GET``
    returned, or null, leaves the stored value untouched, which lets the console
    round-trip the form without knowing the secret. An **empty string** clears
    the stored secret (GAP-061), so a blank form field is sent as the mask.

    The audit record names the fields that actually changed — a cleared secret
    included — and never their values (CA-003).

    Args:
        request: The incoming request, used for the capability check and to
            attribute the change.
        body: The connectivity fields to change, keyed as in the ``GET``
            response.

    Returns:
        The saved connectivity profile in the same masked shape the ``GET``
        returns.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    before = _connectivity.load().to_public()
    user = _platform_user(request)
    actor = user.username if user else "admin"
    profile = _connectivity.update(body, actor=actor)
    after = profile.to_public()
    # Secrets are compared only by the presence marker the public shape exposes, which
    # flips when one is stored or cleared; any other value replaces the secret (GAP-061).
    presence = {"proxy_password": "proxy_password", "custom_ca_pem": "custom_ca_configured"}
    changed: list[str] = []
    for key in body:
        if key in presence:
            replaced = body[key] not in (None, "", "***")
            marker = presence[key]
            if replaced or before.get(marker) != after.get(marker):
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


@router.post("/v1/settings/connectivity/test")
def test_connectivity_route(request: Request) -> dict[str, object]:
    """Check that the configured proxy and TLS settings can reach the internet.

    Makes one outbound request through the same HTTP client the cloud LLM
    providers use, so a failure here is the failure an operator would hit when
    saving a cloud model. Nothing is stored and no credential is sent.

    Args:
        request: The incoming request, used for the capability check.

    Returns:
        A mapping with ``status`` (``"passed"`` or ``"failed"``), ``category``
        (null when it passed, otherwise the classified failure kind such as a
        proxy, TLS or DNS problem) and a human-readable ``message``.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    from ado2gh.api.llm.http_llm import build_cloud_llm_http_client
    from ado2gh.api.llm.model_validation import _classify_error

    try:
        with build_cloud_llm_http_client() as client:
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


@router.get("/v1/settings/llm-models/providers")
def list_llm_provider_types(request: Request) -> dict[str, object]:
    """List the LLM provider types a model can be configured against.

    Drives the provider picker in the console: each spec says what the provider
    is called, which fields it needs and how it authenticates.

    Args:
        request: The incoming request, used for the capability check.

    Returns:
        A mapping with a ``providers`` array, one entry per registered provider
        type.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    from ado2gh.api.llm.llm_provider_registry import list_provider_specs

    return {"providers": list_provider_specs()}


# POST, not GET: the provider api_key is a credential and a URL is the least private
# part of a request (browser history, HAR exports, proxy access logs -- CWE-598).
# Same body-carried shape as /v1/settings/llm-models/validate below.
@router.post("/v1/settings/llm-models/catalog")
def get_llm_catalog(request: Request, body: dict) -> dict[str, object]:
    """List the models a provider offers, for the model picker in the console.

    This is a lookup, not a mutation, but it is a ``POST`` on purpose: the
    credential it needs travels in the body rather than in a query string, which
    would otherwise be recorded in browser history, HAR exports and proxy access
    logs (CWE-598, GAP-012). The credential is used for this one call and is
    never stored or echoed back.

    The live provider listing is preferred; when it cannot be reached the
    bundled preset list is returned instead, and the response says which.

    Args:
        request: The incoming request, used for the capability check.
        body: The provider lookup. ``provider`` is the provider type; the
            optional credential field is used to authenticate the listing call,
            and ``base_url`` overrides the endpoint for self-hosted or
            gateway-fronted providers.

    Returns:
        A mapping with the catalog entries for that provider plus the
        provenance of the listing — whether it came from the provider live or
        from the bundled presets.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            400 when the provider is unknown or the lookup arguments are invalid.
    """
    require_manage_models(request)
    from ado2gh.api.llm.model_catalog import list_catalog

    try:
        return list_catalog(
            provider=body.get("provider", ""),
            api_key=body.get("api_key", ""),
            base_url=body.get("base_url", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/settings/llm-models/validate")
def validate_llm_draft(request: Request, body: dict) -> dict[str, object]:
    """Test an unsaved model configuration before an operator commits to it.

    Takes the same body as creating a model and performs a live call against the
    provider, so the console can show whether the settings work without leaving
    a broken model behind. Nothing is persisted. The credential travels in the
    body for the same reason as the catalog lookup and is never stored or echoed
    back.

    The audit record keeps the outcome only — never the credential or the
    endpoint's response (CA-003).

    Args:
        request: The incoming request, used for the capability check and to
            attribute the audit record.
        body: The draft model configuration to test: provider type, model
            identifier, the credential to authenticate with, and any endpoint
            override.

    Returns:
        A mapping with ``status`` (whether the model answered), an optional
        failure ``category`` and a human-readable message.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    from ado2gh.api.llm.model_validation import validate_draft

    result = validate_draft(body)
    user = _platform_user(request)
    write_profile_audit(
        "llm.model.validated",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={"status": result["status"], "category": result.get("category")},
    )
    return result


@router.post("/v1/settings/llm-models/{model_id}/validate")
def validate_llm_saved(model_id: str, request: Request) -> dict[str, object]:
    """Re-test a model that is already saved, using its stored credential.

    Used to re-check a model after a provider outage, a key rotation or a
    connectivity change. The stored credential is read internally and never
    appears in the request or the response.

    Args:
        model_id: Identifier of the saved model to validate.
        request: The incoming request, used for the capability check and to
            attribute the audit record.

    Returns:
        A mapping with ``status`` (whether the model answered), an optional
        failure ``category`` and a human-readable message.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no model carries that identifier.
    """
    require_manage_models(request)
    from ado2gh.api.llm.model_validation import validate_saved

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


@router.get("/v1/settings/llm-models")
def list_llm_models(request: Request) -> dict[str, object]:
    """List the configured LLM models.

    The only settings route open to non-administrators, because every caller
    needs to know which models the agent can be pointed at. What comes back
    depends on the caller: an administrator sees every model, including the ones
    that are disabled or have never validated; anyone else sees only the enabled
    ones. Both views are equally redacted — no stored credential is returned to
    either, only a mask saying whether one is set.

    Args:
        request: The incoming request, used to decide which view to return.

    Returns:
        A mapping with a ``models`` array of redacted model configurations.
    """
    user = _platform_user(request)
    if user and user.role == PlatformRole.ADMIN:
        return {"models": [m.to_public() for m in _llm_models.load()]}
    return {"models": _llm_models.list_public()}


@router.post("/v1/settings/llm-models")
def create_llm_model(request: Request, body: dict) -> dict[str, object]:
    """Save a new LLM model, or overwrite one when the body carries its id.

    A model created already enabled, or already set as the agent default, is
    audited as enabled on top of the creation record, so the log shows when a
    model first became usable rather than only when it was added.

    Args:
        request: The incoming request, used for the capability check and to
            attribute the audit records.
        body: The model configuration: provider type, model identifier, display
            name, the credential to store, any endpoint override, and the
            enabled and agent-default flags.

    Returns:
        The saved model in its redacted form — every configuration field, with
        the credential replaced by a mask when one is stored.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            400 when the configuration is invalid, 404 when it refers to
            something that does not exist.
    """
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


@router.put("/v1/settings/llm-models/{model_id}")
def update_llm_model(model_id: str, request: Request, body: dict) -> dict[str, object]:
    """Update a saved LLM model.

    The path identifier wins over any ``id`` in the body, so an update can never
    be redirected at a different model. Turning a validated model on, or making
    it the agent default, writes an audit record; a change that leaves both
    flags as they were does not.

    Args:
        model_id: Identifier of the model to update.
        request: The incoming request, used for the capability check and to
            attribute the audit record.
        body: The model fields to change, keyed as in the create body. Leaving
            the credential field at its masked value keeps the stored one.

    Returns:
        The saved model in its redacted form, with the credential replaced by a
        mask when one is stored.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no model carries that identifier, 400 when the
            configuration is invalid.
    """
    require_manage_models(request)
    try:
        before = _llm_models.get(model_id)
        before_enabled = before.enabled if before else False
        before_default = before.default_for_agent if before else False
        body["id"] = model_id
        model = _llm_models.upsert(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Model not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _maybe_audit_model_enabled(
        request,
        before_enabled=before_enabled,
        before_default=before_default,
        model=model,
    )
    return model.to_public()


@router.delete("/v1/settings/llm-models/{model_id}")
def delete_llm_model(model_id: str, request: Request) -> dict[str, object]:
    """Delete a saved LLM model and the credential stored with it.

    Args:
        model_id: Identifier of the model to delete.
        request: The incoming request, used for the capability check and to
            attribute the audit record.

    Returns:
        A mapping whose ``deleted`` key carries the identifier that was removed.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no model carries that identifier.
    """
    require_manage_models(request)
    try:
        _llm_models.delete(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Model not found") from exc
    user = _platform_user(request)
    write_profile_audit(
        "llm_model.deleted",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={"model_id": model_id},
    )
    return {"deleted": model_id}


@router.get("/v1/settings/cloud-credentials")
def list_cloud_credentials(request: Request) -> dict[str, object]:
    """List the cloud credential sources the host offers, and their approval state.

    These are the ambient credentials a cloud LLM provider can run on — an
    instance role, a workload identity, a CLI login — rather than keys the
    platform stores. Only their presence and shape is ever reported; no
    credential value is read, held or returned (CA-003).

    This reports the last recorded detection and never probes the host itself.
    Re-probing is ``POST /v1/settings/cloud-credentials/scan``, which runs the
    same probe and writes an audit record for it (CA-004).

    Args:
        request: The incoming request, used for the capability check.

    Returns:
        A mapping with a ``sources`` array: per provider, the service name,
        approval status, detection completeness, the primary and alternate
        authentication methods, region, project and endpoint, which fields are
        still missing, the last scan and probe results, and the approval or
        rejection actor, time and reason.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    sources = _cloud_credentials.list_sources()
    return {"sources": [s.to_public() for s in sources]}


@router.post("/v1/settings/cloud-credentials/scan")
def scan_cloud_credentials(request: Request) -> dict[str, object]:
    """Re-probe the host for cloud credential sources and return what was found.

    Detection only: it records which providers the machine can authenticate to
    and how complete each one is, never a credential value (CA-003). This is the
    only way to re-probe the host, and it writes an audit record naming the
    providers detected.

    Args:
        request: The incoming request, used for the capability check and to
            attribute the audit record.

    Returns:
        A mapping with a ``sources`` array in the same shape the listing
        endpoint returns, refreshed by this scan.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``.
    """
    require_manage_models(request)
    sources = _cloud_credentials.scan()
    user = _platform_user(request)
    actor = user.username if user else "admin"
    write_profile_audit(
        "cloud_credentials.scanned",
        profile_id="_platform",
        actor=actor,
        payload={"providers": [s.provider for s in sources]},
    )
    return {"sources": [s.to_public() for s in sources]}


@router.patch("/v1/settings/cloud-credentials/{provider}")
def patch_cloud_credentials(provider: str, request: Request, body: dict) -> dict[str, object]:
    """Fill in the configuration a detected credential source is missing.

    Detection can find that a provider is reachable but leave gaps — no region,
    no project, no endpoint — which the source reports in its missing fields.
    This is how an administrator supplies them. It takes configuration only: a
    credential value is neither expected nor stored here (CA-003).

    The audit record names the fields that were supplied, not their values.

    Args:
        provider: Key of the cloud provider whose source is being amended.
        request: The incoming request, used for the capability check and to
            attribute the audit record.
        body: The configuration fields to set, keyed as in the source payload.

    Returns:
        The amended credential source, in the same shape the listing endpoint
        returns.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no source was detected for that provider, 400 when a
            supplied field is not one this provider accepts.
    """
    require_manage_models(request)
    try:
        source = _cloud_credentials.patch_provider(provider, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Credential not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = _platform_user(request)
    write_profile_audit(
        "cloud_credentials.updated",
        profile_id="_platform",
        actor=user.username if user else "admin",
        payload={"provider": provider, "fields": list(body.keys())},
    )
    return source.to_public()


@router.post("/v1/settings/cloud-credentials/{provider}/approve")
def approve_cloud_credentials(provider: str, request: Request) -> dict[str, object]:
    """Approve a detected credential source so models may run on it.

    Approval is the gate between "the host can authenticate to this provider"
    and "the agent is allowed to use it". The source is probed while approving,
    so one that cannot actually reach the provider is refused. The approving
    actor and time are recorded on the source and in the audit log.

    Args:
        provider: Key of the cloud provider to approve.
        request: The incoming request, used for the capability check and to
            record who approved.

    Returns:
        The approved credential source, in the same shape the listing endpoint
        returns, now carrying the approval actor and timestamp.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no source was detected for that provider, 400 when the
            source is incomplete or the probe failed.
    """
    require_manage_models(request)
    from ado2gh.api.credentials.cloud_credential_probe import probe_provider

    user = _platform_user(request)
    actor = user.username if user else "admin"
    try:
        source = _cloud_credentials.approve(provider, actor=actor, probe_fn=probe_provider)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Credential not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_profile_audit(
        "cloud_credentials.approved",
        profile_id="_platform",
        actor=actor,
        payload={"provider": provider},
    )
    return source.to_public()


@router.post("/v1/settings/cloud-credentials/{provider}/reject")
def reject_cloud_credentials(
    provider: str, request: Request, body: dict | None = None,
) -> dict[str, object]:
    """Reject a detected credential source so no model may run on it.

    The source stays listed, marked rejected with the reason, so the decision is
    visible rather than looking like the provider was never detected. The
    rejecting actor, time and reason are recorded on the source and in the audit
    log.

    Args:
        provider: Key of the cloud provider to reject.
        request: The incoming request, used for the capability check and to
            record who rejected.
        body: Optional payload whose ``reason`` explains the rejection. Omitting
            it records an empty reason.

    Returns:
        The rejected credential source, in the same shape the listing endpoint
        returns, now carrying the rejection actor, timestamp and reason.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no source was detected for that provider.
    """
    require_manage_models(request)
    reason = (body or {}).get("reason", "")
    user = _platform_user(request)
    actor = user.username if user else "admin"
    try:
        source = _cloud_credentials.reject(provider, actor=actor, reason=reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Credential not found") from exc
    write_profile_audit(
        "cloud_credentials.rejected",
        profile_id="_platform",
        actor=actor,
        payload={"provider": provider, "reason": reason},
    )
    return source.to_public()


@router.delete("/v1/settings/cloud-credentials/{provider}")
def revoke_cloud_credentials(provider: str, request: Request) -> dict[str, object]:
    """Withdraw an approval, returning the source to its unapproved state.

    Use this when a source that was previously trusted should no longer back a
    model — the host itself is untouched, only the platform's permission to use
    it is withdrawn.

    Args:
        provider: Key of the cloud provider whose approval is being withdrawn.
        request: The incoming request, used for the capability check and to
            attribute the audit record.

    Returns:
        A mapping whose ``deleted`` key carries the provider key that was
        revoked.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 when no source was detected for that provider.
    """
    require_manage_models(request)
    user = _platform_user(request)
    actor = user.username if user else "admin"
    try:
        _cloud_credentials.revoke(provider)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Credential not found") from exc
    write_profile_audit(
        "cloud_credentials.revoked",
        profile_id="_platform",
        actor=actor,
        payload={"provider": provider},
    )
    return {"deleted": provider}


@router.get("/v1/settings/cloud-credentials/platform-model")
def get_platform_model_status(request: Request) -> dict[str, object]:
    """Report whether the model shipped with this deployment is ready to use.

    Some deployments supply their own model rather than expecting an operator to
    configure one. This says how far along that model is: an operator still has
    to see it validate, enable it, and approve the cloud credential source
    behind it before the agent can run on it.

    Args:
        request: The incoming request, used for the capability check.

    Returns:
        A mapping with the provider, model identifier and region, the identifier
        the model is stored under, its validation status, whether an operator
        has enabled it, and whether its credential source is approved.

    Raises:
        HTTPException: 401 without an identity, 403 without ``can_manage_models``,
            404 with ``platform_model_not_configured`` when this deployment
            supplies no model.
    """
    require_manage_models(request)
    from ado2gh.api.llm.platform_managed_model import platform_model_status

    status = platform_model_status()
    if not status:
        raise HTTPException(status_code=404, detail="platform_model_not_configured")
    return status


@router.get("/v1/settings")
def get_settings() -> SettingsResponse:
    """Return every migration profile plus the deployment-wide defaults.

    This is what the console loads to render the settings area. Profile secrets
    are masked: the ADO personal access token and every GitHub token come back
    as placeholders saying a value is stored, never as the value itself
    (CA-003).

    Returns:
        The active profile's identifier, every migration profile with its
        governance status and masked credentials, and the advanced settings that
        apply across the deployment.
    """
    s = _settings.load()
    return SettingsResponse(**s.to_public())


@router.put("/v1/settings/advanced")
def update_advanced(req: AdvancedSettingsRequest) -> dict[str, object]:
    """Change the deployment-wide execution defaults.

    Only the fields actually sent are applied; omitting one leaves it as it was.
    ``dry_run_default`` is the notable one — it decides whether a console run
    that names no mode rehearses or migrates for real.

    Args:
        req: The advanced settings to change. Every field is optional.

    Returns:
        The full advanced-settings record after the change, one key per field.
    """
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    adv = _settings.update_advanced(data)
    return advanced_settings_as_dict(adv)


@router.get("/v1/settings/phases")
def get_phases(profile_id: str | None = None) -> dict[str, object]:
    """Return the rollout phases and how well their risk bands fit the repositories.

    Args:
        profile_id: Profile whose scanned repositories the counts and coverage
            are computed from. Omit it to count across every profile.

    Returns:
        The configured phases with their risk bands, a coverage report saying
        whether the bands span the observed risk scores without gaps or
        overlaps, the number of repositories assigned to each phase, and the
        phase new repositories default to.
    """
    return _settings.phases_payload(profile_id)


@router.put("/v1/settings/phases")
def update_phases(req: PhasesUpdateRequest) -> dict[str, object]:
    """Replace the rollout phases with exactly the set supplied.

    Any phase absent from the body is removed, so a phase that still has
    repositories assigned needs a matching entry in ``removals`` naming where
    those repositories go. Setting ``span_to_scan`` first widens the supplied
    risk bands to cover the full range of risk scores seen in the profile's
    latest scan, so no scanned repository is left outside every phase.

    Args:
        req: The complete new set of phases, the destinations for any phase
            being removed, whether to stretch the bands over the scan, and the
            profile the counts are computed against.

    Returns:
        The saved phases in the same shape the ``GET`` returns, including the
        refreshed coverage report and repository counts.

    Raises:
        HTTPException: 400 when the phases are inconsistent — for example a
            removed phase still holding repositories with no destination given.
    """
    update = (
        _settings.update_phases_spanning_scan
        if req.span_to_scan
        else _settings.update_phases
    )
    try:
        return update(
            [p.model_dump() for p in req.phases],
            removals=[r.model_dump() for r in req.removals],
            profile_id=req.profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
