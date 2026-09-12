"""Shared state and helpers for accelerator API route modules."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from ado2gh.api.accelerator import Accelerator
from ado2gh.api.contracts import RunWaveRequest
from ado2gh.api.credentials.credential_validation import validate_ado_pat as _validate_ado_pat
from ado2gh.api.credentials.credential_validation import validate_github_token as _validate_github_token
from ado2gh.api.live_approval_store import LiveApprovalStore
from ado2gh.api.migration_scan import (
    load_scan_results as _load_scan_results,
)
from ado2gh.api.migration_scan import (
    persist_scan_results as _persist_scan_results,
)
from ado2gh.api.migration_scan import (
    scan_with_credentials as _scan_with_credentials,
)
from ado2gh.api.pipeline_runner import PipelineRunner, PipelineRunStore
from ado2gh.api.platform_rbac import require_manage_settings
from ado2gh.api.profile_governance import ProfileGovernanceError
from ado2gh.api.settings_store import SettingsStore

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from pathlib import Path

    from ado2gh.api.llm.llm_model_store import LLMModelConfig
    from ado2gh.api.settings_models import AdvancedSettings, MigrationProfile
    from ado2gh.auth.models import PlatformUser


def validate_ado_pat(*args: object, **kwargs: object) -> dict[str, Any]:
    """Lazy wrapper — picks up patches on services.accelerator_api.main.

    Args:
        *args: Positional arguments forwarded unchanged to the validator.
        **kwargs: Keyword arguments forwarded unchanged to the validator.

    Returns:
        The validator's verdict: whether the Azure DevOps organisation could be
        reached, a human-readable message, discovered counts and any warnings.
    """
    try:
        from services.accelerator_api import main as _m
        return _m.validate_ado_pat(*args, **kwargs)
    except (ImportError, AttributeError):
        return _validate_ado_pat(*args, **kwargs)


def validate_github_token(*args: object, **kwargs: object) -> dict[str, Any]:
    """Lazy wrapper — picks up patches on services.accelerator_api.main.

    Args:
        *args: Positional arguments forwarded unchanged to the validator.
        **kwargs: Keyword arguments forwarded unchanged to the validator.

    Returns:
        The validator's verdict: whether GitHub accepted the credential, a
        human-readable message, the granted scopes and any warnings.
    """
    try:
        from services.accelerator_api import main as _m
        return _m.validate_github_token(*args, **kwargs)
    except (ImportError, AttributeError):
        return _validate_github_token(*args, **kwargs)


def scan_with_credentials(*args: object, **kwargs: object) -> dict[str, Any]:
    """Lazy wrapper — picks up patches on services.accelerator_api.main.

    Args:
        *args: Positional arguments forwarded unchanged to the scanner.
        **kwargs: Keyword arguments forwarded unchanged to the scanner.

    Returns:
        The scan results: the discovered repositories with their risk scores and
        phase assignments, plus the roll-up totals the console displays.
    """
    try:
        from services.accelerator_api import main as _m
        return _m.scan_with_credentials(*args, **kwargs)
    except (ImportError, AttributeError):
        return _scan_with_credentials(*args, **kwargs)


def load_scan_results(*args: object, **kwargs: object) -> dict[str, Any] | None:
    """Lazy wrapper — picks up patches on services.accelerator_api.main.

    Args:
        *args: Positional arguments forwarded unchanged to the loader.
        **kwargs: Keyword arguments forwarded unchanged to the loader.

    Returns:
        The profile's stored scan results, or ``None`` when that profile has
        never been scanned.
    """
    try:
        from services.accelerator_api import main as _m
        return _m.load_scan_results(*args, **kwargs)
    except (ImportError, AttributeError):
        return _load_scan_results(*args, **kwargs)


def persist_scan_results(*args: object, **kwargs: object) -> Path:
    """Lazy wrapper — picks up patches on services.accelerator_api.main.

    Args:
        *args: Positional arguments forwarded unchanged to the writer.
        **kwargs: Keyword arguments forwarded unchanged to the writer.

    Returns:
        Path of the JSON backup written alongside the state-database rows.
    """
    try:
        from services.accelerator_api import main as _m
        return _m.persist_scan_results(*args, **kwargs)
    except (ImportError, AttributeError):
        return _persist_scan_results(*args, **kwargs)


def _accel(db_path: str = "migration_state.db") -> Accelerator:
    """Build an accelerator bound to one state database.

    Args:
        db_path: Path to the state database the accelerator should read and
            write.

    Returns:
        An accelerator ready to run waves against that database.
    """
    return Accelerator(db_path=db_path)


def _config_path() -> str:
    """Resolve the migration config file the process should use.

    Returns:
        The path from ``ADO2GH_CONFIG``, or ``migration.yaml`` when it is unset.
    """
    return os.environ.get("ADO2GH_CONFIG", "migration.yaml")


def _platform_user(request: Request) -> PlatformUser | None:
    """Read the signed-in platform user off the request.

    Args:
        request: Incoming request, populated by the authentication middleware.

    Returns:
        The signed-in user, or ``None`` when the request carries no session.
    """
    return getattr(request.state, "platform_user", None)


def _require_admin(request: Request) -> None:
    """Refuse the request unless the caller may change deployment settings.

    Args:
        request: Incoming request, used to identify the signed-in caller.

    Raises:
        HTTPException: 403 when the caller lacks the settings-management
            capability.
    """
    require_manage_settings(request)


def _live_store() -> LiveApprovalStore:
    """Open the live-execution approval store.

    Returns:
        A store bound to the configured state database.
    """
    return LiveApprovalStore()


def _governance_http_error(exc: ProfileGovernanceError) -> HTTPException:
    """Translate a profile governance refusal into the HTTP error to raise.

    Args:
        exc: The governance error raised while checking a profile.

    Returns:
        An ``HTTPException`` carrying the governance code as its detail: 404 for
        an unknown profile, 409 for a conflict the caller could resolve, and 403
        for everything else.
    """
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


def _require_profile(profile_id: str) -> MigrationProfile:
    """Look up a migration profile or fail the request with 404.

    Args:
        profile_id: Identifier of the migration profile to load.

    Returns:
        The stored migration profile.

    Raises:
        HTTPException: 404 when no profile carries that identifier.
    """
    p = _settings.get_profile(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Migration profile not found")
    return p


def _require_active_profile(profile_id: str) -> MigrationProfile:
    """Look up a migration profile and refuse it unless it may run migrations.

    Used by the endpoints that start work against a profile, so that an
    archived, suspended or unapproved profile cannot be driven by mistake.

    Args:
        profile_id: Identifier of the migration profile to load.

    Returns:
        The stored migration profile, known to be runnable.

    Raises:
        HTTPException: 404 when no profile carries that identifier, or the
            governance status code (403/404/409) when the profile exists but
            is not allowed to run.
    """
    from ado2gh.api.profile_governance import assert_profile_active_for_run
    p = _require_profile(profile_id)
    try:
        assert_profile_active_for_run(p)
    except ProfileGovernanceError as exc:
        raise _governance_http_error(exc) from exc
    return p


def _execute_approved_migrate(ctx: dict) -> None:
    """Run the migration wave an approver has just released.

    Registered with ``LiveApprovalStore`` so that approving a ``migrate_job``
    starts the work without the caller posting again.

    Args:
        ctx: The approval's stored context, holding the fields of the original
            run-wave request.
    """
    req = RunWaveRequest(**ctx)
    _accel(req.db_path).run_wave(req)


def _execute_approved_pipeline(ctx: dict) -> None:
    """Start the pipeline run an approver has just released.

    Registered with ``LiveApprovalStore`` so that approving a ``pipeline_run``
    starts the parked run without the caller posting again. Does nothing when
    the approval carries no run id.

    Args:
        ctx: The approval's stored context, holding ``run_id`` and the ``steps``
            the run should execute.
    """
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
    model: LLMModelConfig,
) -> None:
    """Write an audit record when a validated LLM model is turned on.

    Only a model that has passed validation is auditable, and only a transition
    into enabled or agent-default is recorded — turning one off, or saving with
    neither flag changed, writes nothing.

    Args:
        request: Incoming request, used to attribute the change to a caller.
        before_enabled: Whether the model was enabled before the save.
        before_default: Whether the model was the agent default before the save.
        model: The model configuration as it stands after the save.
    """
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


def advanced_settings_as_dict(adv: AdvancedSettings) -> dict[str, Any]:
    """Flatten the deployment-wide advanced settings into a JSON-ready mapping.

    Args:
        adv: The advanced-settings record to convert.

    Returns:
        One key per settings field, with nested dataclasses expanded to
        dictionaries, ready to be returned as an API response body.
    """
    from dataclasses import asdict
    return asdict(adv)
