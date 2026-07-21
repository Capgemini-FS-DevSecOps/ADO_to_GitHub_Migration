"""Sync deploy-time platform-supplied LLM model SKUs into the admin model store."""
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
    provider: str
    model_id: str
    region: str = ""
    endpoint: str = ""
    ambient_provider: str = ""
    synced_model_id: str = ""


def read_platform_config() -> PlatformSuppliedModelConfig | None:
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
