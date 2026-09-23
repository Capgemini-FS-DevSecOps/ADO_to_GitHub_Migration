"""Agent-facing model list (BYOK + approved ambient platform models)."""
from __future__ import annotations

from typing import Any

from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore
from ado2gh.api.llm.llm_model_store import LLMModelStore
from ado2gh.api.llm.llm_provider_registry import get_provider_spec


def _provider_label(provider: str) -> str:
    """Return the human-readable display name for a provider key.

    Args:
        provider: Provider key such as the identifier stored on a model record.

    Returns:
        The label declared by the provider registry, or the provider key with
        underscores replaced by spaces and title-cased when the registry has no
        entry for it.
    """
    spec = get_provider_spec(provider)
    if spec:
        return spec.label
    return provider.replace("_", " ").title()


def list_agent_models() -> dict[str, Any]:
    """List the language models the agent is currently allowed to use.

    A model is included only when it is enabled and its validation has passed.
    Models that draw on ambient cloud credentials are additionally suppressed
    unless an administrator has approved that provider's credential source.

    Returns:
        A mapping with two keys. ``models`` is the list of selectable models,
        each entry carrying its identifier, provider key and display label,
        the provider-side model id, whether it uses ambient or bring-your-own
        credentials, the backing cloud provider and its approval state, whether
        the platform supplies it, and its enabled and validation status.
        ``default_model_id`` is the configured default when that model is still
        selectable, otherwise the first selectable model, or None when none are.
    """
    store = LLMModelStore()
    creds = CloudCredentialsStore()
    models_out: list[dict[str, Any]] = []
    default_id: str | None = None
    for model in store.load():
        if not model.enabled or model.validation_status != "passed":
            continue
        if model.provider not in ("stub", "offline") and model.validation_status != "passed":
            continue
        cloud_provider = None
        source_approved = None
        if model.credential_mode == "ambient":
            cloud_provider = model.ambient_provider or None
            source_approved = creds.is_approved(model.ambient_provider or "")
            if not source_approved:
                continue
        models_out.append(
            {
                "id": model.id,
                "provider": model.provider,
                "provider_label": _provider_label(model.provider),
                "model_id": model.model_id,
                "credential_mode": model.credential_mode,
                "cloud_provider": cloud_provider,
                "platform_supplied": model.platform_supplied,
                "source_approved": source_approved,
                "enabled": model.enabled,
                "validation_status": model.validation_status,
            }
        )
    default = store.get_default_model()
    if default and any(m["id"] == default.id for m in models_out):
        default_id = default.id
    elif models_out:
        default_id = models_out[0]["id"]
    return {"models": models_out, "default_model_id": default_id}
