"""Settings, LLM, connectivity, and cloud credential route handlers."""
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
    asdict_adv,
    _settings,
)

router = APIRouter()


@router.get("/v1/settings/connectivity")
def get_connectivity(request: Request):
    require_manage_models(request)
    return _connectivity.load().to_public()


@router.put("/v1/settings/connectivity")
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


@router.post("/v1/settings/connectivity/test")
def test_connectivity_route(request: Request):
    require_manage_models(request)
    from ado2gh.api.llm.http_llm import build_llm_http_client
    from ado2gh.api.llm.model_validation import _classify_error

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


@router.get("/v1/settings/llm-models/providers")
def list_llm_provider_types(request: Request):
    require_manage_models(request)
    from ado2gh.api.llm.llm_provider_registry import list_provider_specs

    return {"providers": list_provider_specs()}


@router.get("/v1/settings/llm-models/catalog")
def get_llm_catalog(
    request: Request,
    provider: str,
    api_key: str = "",
    base_url: str = "",
):
    require_manage_models(request)
    from ado2gh.api.llm.model_catalog import list_catalog

    try:
        return list_catalog(provider=provider, api_key=api_key, base_url=base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/settings/llm-models/validate")
def validate_llm_draft(request: Request, body: dict):
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
def validate_llm_saved(model_id: str, request: Request):
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
def list_llm_models(request: Request):
    user = _platform_user(request)
    if user and user.role == PlatformRole.ADMIN:
        return {"models": [m.to_public() for m in _llm_models.load()]}
    return {"models": _llm_models.list_public()}


@router.post("/v1/settings/llm-models")
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


@router.put("/v1/settings/llm-models/{model_id}")
def update_llm_model(model_id: str, request: Request, body: dict):
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
def delete_llm_model(model_id: str, request: Request):
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
def list_cloud_credentials(request: Request, scan: bool = False):
    require_manage_models(request)
    if scan:
        _cloud_credentials.scan()
    sources = _cloud_credentials.list_sources()
    return {"sources": [s.to_public() for s in sources]}


@router.post("/v1/settings/cloud-credentials/scan")
def scan_cloud_credentials(request: Request):
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
def patch_cloud_credentials(provider: str, request: Request, body: dict):
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
def approve_cloud_credentials(provider: str, request: Request):
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
def reject_cloud_credentials(provider: str, request: Request, body: dict | None = None):
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
def revoke_cloud_credentials(provider: str, request: Request):
    require_manage_models(request)
    user = _platform_user(request)
    actor = user.username if user else "admin"
    try:
        _cloud_credentials.revoke(provider, actor=actor)
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
def get_platform_model_status(request: Request):
    require_manage_models(request)
    from ado2gh.api.llm.platform_managed_model import platform_model_status

    status = platform_model_status()
    if not status:
        raise HTTPException(status_code=404, detail="platform_model_not_configured")
    return status


@router.get("/v1/settings")
def get_settings():
    s = _settings.load()
    return SettingsResponse(**s.to_public())


@router.put("/v1/settings/advanced")
def update_advanced(req: AdvancedSettingsRequest):
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    adv = _settings.update_advanced(data)
    return asdict_adv(adv)


@router.get("/v1/settings/phases")
def get_phases(profile_id: str | None = None):
    return _settings.phases_payload(profile_id)


@router.put("/v1/settings/phases")
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
