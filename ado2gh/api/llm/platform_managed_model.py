"""Sync deploy-time platform-supplied language model SKUs into the admin model store."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore

AMBIENT_PROVIDER_BY_LLM = {
    "bedrock": "aws",
    "foundry": "foundry",
    "vertex": "gcp",
}

STABLE_MODEL_IDS = {
    "bedrock": "platform-bedrock",
    "foundry": "platform-foundry",
    "vertex": "platform-vertex",
}


@dataclass
class PlatformSuppliedModelConfig:
    """A language model the deployment supplies, rather than an operator.

    Describes a model wired up at deploy time against a managed cloud provider:
    which provider and model to call, where it lives, and which ambient
    credential source authorises the calls. Holds no credential of its own.
    """

    provider: str
    model_id: str
    region: str = ""
    endpoint: str = ""
    ambient_provider: str = ""
    synced_model_id: str = ""


def read_platform_config() -> PlatformSuppliedModelConfig | None:
    """Read the platform-supplied model, if the deployment configured one.

    Inspects the deployment environment for a managed Bedrock, Foundry or Vertex
    model, in that order, and takes the first one configured.

    Returns:
        PlatformSuppliedModelConfig | None: The configured provider, model
        identifier, region and endpoint together with the ambient credential
        source that authorises it and the stable identifier it is stored under,
        or None when the deployment supplies no model and operators must
        configure their own.
    """
    bedrock_model = os.environ.get("ADO2GH_BEDROCK_MODEL_ID", "").strip()
    if bedrock_model:
        return PlatformSuppliedModelConfig(
            provider="bedrock",
            model_id=bedrock_model,
            region=os.environ.get("AWS_REGION", "").strip(),
            ambient_provider="aws",
            synced_model_id=STABLE_MODEL_IDS["bedrock"],
        )
    foundry_model = os.environ.get("ADO2GH_FOUNDRY_MODEL_ID", "").strip()
    if foundry_model:
        return PlatformSuppliedModelConfig(
            provider="foundry",
            model_id=foundry_model,
            endpoint=(
                os.environ.get("ADO2GH_FOUNDRY_ENDPOINT")
                or os.environ.get("AZURE_OPENAI_ENDPOINT")
                or ""
            ).strip(),
            region=os.environ.get("AZURE_REGION", "").strip(),
            ambient_provider="foundry",
            synced_model_id=STABLE_MODEL_IDS["foundry"],
        )
    vertex_model = os.environ.get("ADO2GH_VERTEX_MODEL_ID", "").strip()
    if vertex_model:
        return PlatformSuppliedModelConfig(
            provider="vertex",
            model_id=vertex_model,
            region=os.environ.get("GOOGLE_CLOUD_REGION", "").strip(),
            ambient_provider="gcp",
            synced_model_id=STABLE_MODEL_IDS["vertex"],
        )
    return None


def sync_on_startup() -> PlatformSuppliedModelConfig | None:
    """Reconcile the deployment's platform-supplied model into the model store.

    Runs at service startup. The model is written under a stable identifier so
    repeated startups update the same record rather than accumulating
    duplicates, and the operator's enable and default choices are preserved
    across restarts. The record is marked read-only and ambient-credentialed,
    so no credential is stored for it.

    Returns:
        PlatformSuppliedModelConfig | None: The configuration that was synced,
        or None when the deployment supplies no model and nothing was written.
    """
    cfg = read_platform_config()
    if not cfg:
        return None
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    store = LLMModelStore()
    display = {
        "bedrock": "Platform Bedrock",
        "foundry": "Platform Foundry",
        "vertex": "Platform Vertex",
    }.get(cfg.provider, f"Platform {cfg.provider}")
    existing = store.get(cfg.synced_model_id)
    payload: dict[str, Any] = {
        "id": cfg.synced_model_id,
        "display_name": display,
        "provider": cfg.provider,
        "model_id": cfg.model_id,
        "enabled": existing.enabled if existing else False,
        "default_for_agent": existing.default_for_agent if existing else False,
        "credential_mode": "ambient",
        "platform_supplied": True,
        "ambient_provider": cfg.ambient_provider,
        "base_url": cfg.endpoint or "",
        "catalog_source": "preset",
        "catalog_label": "platform-supplied",
        "api_key": "",
    }
    if existing:
        store.upsert(payload, model_id=cfg.synced_model_id)
    else:
        store.upsert(payload)
    return cfg


def platform_model_payload() -> dict[str, Any] | None:
    """Describe the platform-supplied model for the settings screen.

    Returns:
        dict[str, Any] | None: The provider, model identifier, region and
        endpoint (each None when not configured), the stable identifier the
        model is stored under, a read-only marker so the UI blocks edits, and an
        ``available`` flag that is true only when the credential source is
        approved, the model is enabled and it has passed validation. None when
        the deployment supplies no model.
    """
    cfg = read_platform_config()
    if not cfg:
        return None
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    store = LLMModelStore()
    model = store.get(cfg.synced_model_id)
    creds = CloudCredentialsStore()
    approved = creds.is_approved(cfg.ambient_provider)
    enabled = bool(model and model.enabled)
    return {
        "provider": cfg.provider,
        "model_id": cfg.model_id,
        "region": cfg.region or None,
        "endpoint": cfg.endpoint or None,
        "read_only": True,
        "synced_model_id": cfg.synced_model_id,
        "available": approved and enabled and bool(model and model.validation_status == "passed"),
    }


def platform_model_status() -> dict[str, Any] | None:
    """Report the readiness of the platform-supplied model.

    Returns:
        dict[str, Any] | None: The provider, model identifier and region, the
        stable identifier the model is stored under, its current validation
        status, whether an operator has enabled it, and whether its ambient
        credential source has been approved — the three conditions an operator
        must satisfy before the agent can use it. None when the deployment
        supplies no model.
    """
    cfg = read_platform_config()
    if not cfg:
        return None
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    model = LLMModelStore().get(cfg.synced_model_id)
    approved = CloudCredentialsStore().is_approved(cfg.ambient_provider)
    return {
        "provider": cfg.provider,
        "model_id": cfg.model_id,
        "region": cfg.region or None,
        "synced_model_id": cfg.synced_model_id,
        "validation_status": model.validation_status if model else "never_validated",
        "enabled": bool(model and model.enabled),
        "source_approved": approved,
    }
