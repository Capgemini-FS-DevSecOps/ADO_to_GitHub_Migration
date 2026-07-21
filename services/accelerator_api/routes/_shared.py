"""Shared state and helpers for accelerator API route modules."""
from __future__ import annotations

import os

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import RunWaveRequest
from ado2gh.api.credentials.credential_validation import validate_ado_pat as _validate_ado_pat
from ado2gh.api.credentials.credential_validation import validate_github_token as _validate_github_token
from ado2gh.api.live_approval_store import LiveApprovalStore
from ado2gh.api.migration_scan import (
    load_scan_results as _load_scan_results,
    persist_scan_results as _persist_scan_results,
    scan_with_credentials as _scan_with_credentials,
)
from ado2gh.api.pipeline_runner import PipelineRunStore, PipelineRunner
from ado2gh.api.platform_rbac import require_manage_settings
from ado2gh.api.profile_governance import ProfileGovernanceError
from ado2gh.api.settings_store import SettingsStore
from fastapi import HTTPException, Request


def validate_ado_pat(*args, **kwargs):
    """Lazy wrapper — picks up patches on services.accelerator_api.main."""
    try:
        from services.accelerator_api import main as _m
        return _m.validate_ado_pat(*args, **kwargs)
    except (ImportError, AttributeError):
        return _validate_ado_pat(*args, **kwargs)


def validate_github_token(*args, **kwargs):
    try:
        from services.accelerator_api import main as _m
        return _m.validate_github_token(*args, **kwargs)
    except (ImportError, AttributeError):
        return _validate_github_token(*args, **kwargs)


def scan_with_credentials(*args, **kwargs):
    try:
        from services.accelerator_api import main as _m
        return _m.scan_with_credentials(*args, **kwargs)
    except (ImportError, AttributeError):
        return _scan_with_credentials(*args, **kwargs)


def load_scan_results(*args, **kwargs):
    try:
        from services.accelerator_api import main as _m
        return _m.load_scan_results(*args, **kwargs)
    except (ImportError, AttributeError):
        return _load_scan_results(*args, **kwargs)


def persist_scan_results(*args, **kwargs):
    try:
        from services.accelerator_api import main as _m
        return _m.persist_scan_results(*args, **kwargs)
    except (ImportError, AttributeError):
        return _persist_scan_results(*args, **kwargs)


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


def _governance_http_error(exc: ProfileGovernanceError) -> HTTPException:
    code = exc.code
    status = 409 if code in ("last_active_profile", "default_replacement_required") else 403
    if code == "not_found":
        status = 404
    return HTTPException(status_code=status, detail=code)


_settings = SettingsStore()
_runner = PipelineRunner(_settings)
_llm_models = __import__("ado2gh.api.llm.llm_model_store", fromlist=["LLMModelStore"]).LLMModelStore()
_cloud_credentials = __import__(
    "ado2gh.api.credentials.cloud_credentials_store", fromlist=["CloudCredentialsStore"]
).CloudCredentialsStore()
_connectivity = __import__(
    "ado2gh.api.connectivity_store", fromlist=["ConnectivityStore"]
).ConnectivityStore()


def _require_profile(profile_id: str, require_active: bool = False):
    from ado2gh.api.profile_governance import assert_profile_active_for_run
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    if require_active:
        try:
            assert_profile_active_for_run(p)
        except ProfileGovernanceError as exc:
            raise _governance_http_error(exc)
    return p


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


def _maybe_audit_model_enabled(
    request: Request,
    *,
    before_enabled: bool,
    before_default: bool,
    model,
) -> None:
    from ado2gh.api.profile_governance import write_profile_audit
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


def asdict_adv(adv):
    from dataclasses import asdict
    return asdict(adv)
