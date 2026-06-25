"""Admin-managed LLM provider configurations (secrets stored locally, never returned)."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

VALIDATION_STATUSES = ("never_validated", "passed", "failed")
CATALOG_SOURCES = ("preset", "live", "override")
CREDENTIAL_MODES = ("byok", "ambient")
AMBIENT_LLM_PROVIDERS = ("bedrock", "foundry", "vertex")


def _path() -> Path:
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "llm_models.json"


@dataclass
class LLMModelConfig:
    id: str
    display_name: str
    provider: str
    model_id: str
    api_key: str = ""
    enabled: bool = False
    default_for_agent: bool = False
    created_at: str = ""
    updated_at: str = ""
    base_url: str = ""
    catalog_source: str = "preset"
    catalog_label: str = ""
    validation_status: str = "never_validated"
    validation_at: str = ""
    validation_category: str = ""
    validation_message: str = ""
    credential_mode: str = "byok"
    platform_supplied: bool = False
    ambient_provider: str = ""
    capabilities: dict[str, Any] | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "provider": self.provider,
            "model_id": self.model_id,
            "api_key": "***" if self.api_key else "",
            "enabled": self.enabled,
            "default_for_agent": self.default_for_agent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "base_url": self.base_url or None,
            "catalog_source": self.catalog_source,
            "catalog_label": self.catalog_label or None,
            "validation_status": self.validation_status,
            "validation_at": self.validation_at or None,
            "validation_category": self.validation_category or None,
            "validation_message": self.validation_message or None,
            "credential_mode": self.credential_mode,
            "platform_supplied": self.platform_supplied,
            "ambient_provider": self.ambient_provider or None,
            "capabilities": self.capabilities or None,
        }


def _parse_model(raw: dict[str, Any], secrets: dict[str, str]) -> LLMModelConfig:
    validation_status = raw.get("validation_status", "never_validated")
    enabled = bool(raw.get("enabled", False))
    if validation_status not in VALIDATION_STATUSES:
        validation_status = "never_validated"
    if validation_status == "never_validated" and enabled and "validation_status" not in raw:
        validation_status = "passed"
    return LLMModelConfig(
        id=raw["id"],
        display_name=raw.get("display_name", raw["id"]),
        provider=raw.get("provider", "stub"),
        model_id=raw.get("model_id", "stub"),
        api_key=secrets.get(raw["id"], ""),
        enabled=enabled,
        default_for_agent=bool(raw.get("default_for_agent", False)),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
        base_url=raw.get("base_url", "") or "",
        catalog_source=raw.get("catalog_source", "preset"),
        catalog_label=raw.get("catalog_label", "") or "",
        validation_status=validation_status,
        validation_at=raw.get("validation_at", "") or "",
        validation_category=raw.get("validation_category", "") or "",
        validation_message=raw.get("validation_message", "") or "",
        credential_mode=raw.get("credential_mode", "byok"),
        platform_supplied=bool(raw.get("platform_supplied", False)),
        ambient_provider=raw.get("ambient_provider", "") or "",
        capabilities=raw.get("capabilities") or None,
    )


def _sensitive_fields_changed(existing: LLMModelConfig, data: dict[str, Any]) -> bool:
    if data.get("provider") and data["provider"] != existing.provider:
        return True
    if "model_id" in data and data["model_id"] != existing.model_id:
        return True
    if "base_url" in data and (data.get("base_url") or "") != existing.base_url:
        return True
    if "catalog_source" in data and data["catalog_source"] != existing.catalog_source:
        return True
    api_key = (data.get("api_key") or "").strip()
    if api_key and api_key != "***":
        return True
    return False


def _ambient_approved(model: LLMModelConfig) -> bool:
    if model.credential_mode != "ambient" or not model.ambient_provider:
        return True
    from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore

    return CloudCredentialsStore().is_approved(model.ambient_provider)


def _assert_enable_gate(model: LLMModelConfig, data: dict[str, Any]) -> None:
    if model.provider in ("stub", "offline"):
        return
    wants_enabled = bool(data.get("enabled", model.enabled))
    wants_default = bool(data.get("default_for_agent", model.default_for_agent))
    if wants_enabled or wants_default:
        if model.credential_mode == "ambient" and not _ambient_approved(model):
            raise ValueError("Cloud credentials must be approved before enable")
        if model.validation_status != "passed":
            raise ValueError("Model must pass validation before enable or default")


class LLMModelStore:
    def __init__(self, path: Path | None = None):
        self.path = path or _path()

    def load(self) -> list[LLMModelConfig]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        secrets = data.get("_secrets", {})
        return [_parse_model(raw, secrets) for raw in data.get("models", [])]

    def save(self, models: list[LLMModelConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        secrets = {m.id: m.api_key for m in models if m.api_key}
        public = [m.to_public() for m in models]
        payload = {"models": public, "_secrets": secrets}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_public(self) -> list[dict[str, Any]]:
        return [m.to_public() for m in self.load() if m.enabled]

    def list_agent_ready_models(self) -> list[LLMModelConfig]:
        ready: list[LLMModelConfig] = []
        for model in self.load():
            if not model.enabled:
                continue
            if model.credential_mode == "ambient" and not _ambient_approved(model):
                continue
            if model.provider in ("stub", "offline"):
                ready.append(model)
                continue
            if model.validation_status == "passed":
                ready.append(model)
        return ready

    def has_agent_ready_model(self) -> bool:
        return bool(self.list_agent_ready_models())

    def get_default_model(self) -> Optional[LLMModelConfig]:
        models = self.load()
        default = next((m for m in models if m.default_for_agent and m.enabled), None)
        if default and (
            default.provider in ("stub", "offline")
            or default.validation_status == "passed"
        ):
            return default
        enabled = [
            m for m in models
            if m.enabled
            and m.provider != "stub"
            and m.validation_status == "passed"
        ]
        return enabled[0] if enabled else None

    def upsert(self, data: dict[str, Any], model_id: str | None = None) -> LLMModelConfig:
        models = self.load()
        now = datetime.now(timezone.utc).isoformat()
        api_key = (data.get("api_key") or "").strip()
        provider = data.get("provider", "openai")
        credential_mode = data.get("credential_mode", "byok")
        platform_supplied = bool(data.get("platform_supplied", False))
        ambient_provider = data.get("ambient_provider", "") or ""
        if (
            provider not in ("stub", "offline", "ollama", *AMBIENT_LLM_PROVIDERS)
            and credential_mode != "ambient"
            and not model_id
        ):
            if not api_key:
                raise ValueError("api_key required for non-stub providers")
        if data.get("catalog_source") == "override":
            from ado2gh.api.connectivity_store import get_connectivity_profile

            if not get_connectivity_profile().allow_custom_model_id:
                raise ValueError("Custom model ID override is disabled")

        if model_id:
            existing = next((m for m in models if m.id == model_id), None)
            if not existing:
                raise KeyError(model_id)
            if existing.platform_supplied:
                if data.get("provider") and data["provider"] != existing.provider:
                    raise ValueError("Platform-supplied model fields are read-only")
                if "model_id" in data and data["model_id"] != existing.model_id:
                    raise ValueError("Platform-supplied model fields are read-only")
            if _sensitive_fields_changed(existing, data):
                existing.validation_status = "never_validated"
                existing.validation_at = ""
                existing.validation_category = ""
                existing.validation_message = ""
            if api_key and api_key != "***":
                existing.api_key = api_key
            if not existing.platform_supplied:
                existing.display_name = data.get("display_name", existing.display_name)
                existing.provider = data.get("provider", existing.provider)
                existing.model_id = data.get("model_id", existing.model_id)
            else:
                existing.display_name = data.get("display_name", existing.display_name)
            if "credential_mode" in data:
                existing.credential_mode = data["credential_mode"]
            if "platform_supplied" in data:
                existing.platform_supplied = bool(data["platform_supplied"])
            if "ambient_provider" in data:
                existing.ambient_provider = data.get("ambient_provider") or ""
            if "base_url" in data:
                existing.base_url = data.get("base_url") or ""
            if "catalog_source" in data:
                existing.catalog_source = data["catalog_source"]
            if "catalog_label" in data:
                existing.catalog_label = data.get("catalog_label") or ""
            if "capabilities" in data:
                existing.capabilities = data.get("capabilities") or None
            _assert_enable_gate(existing, data)
            existing.enabled = bool(data.get("enabled", existing.enabled))
            if "default_for_agent" in data:
                existing.default_for_agent = bool(data["default_for_agent"])
            existing.updated_at = now
            model = existing
        else:
            fixed_id = data.get("id")
            model = LLMModelConfig(
                id=fixed_id or str(uuid.uuid4()),
                display_name=data["display_name"],
                provider=provider,
                model_id=data.get("model_id", data["display_name"]),
                api_key=api_key,
                enabled=bool(data.get("enabled", False)),
                default_for_agent=bool(data.get("default_for_agent", False)),
                created_at=now,
                updated_at=now,
                base_url=data.get("base_url") or "",
                catalog_source=data.get("catalog_source", "preset"),
                catalog_label=data.get("catalog_label") or "",
                credential_mode=credential_mode,
                platform_supplied=platform_supplied,
                ambient_provider=ambient_provider,
            )
            if data.get("validation_status") == "passed":
                model.validation_status = "passed"
                model.validation_at = data.get("validation_at", now)
            if "capabilities" in data:
                model.capabilities = data.get("capabilities") or None
            # T028: Validate supports_tool_calling on registration
            caps = model.capabilities or {}
            if caps.get("supports_tool_calling") is False:
                raise ValueError(
                    "This model does not support tool-calling, which is required for the migration agent."
                )
            _assert_enable_gate(model, data)
            models.append(model)
        if model.default_for_agent:
            for other in models:
                if other.id != model.id:
                    other.default_for_agent = False
        self.save(models)
        return model

    def delete(self, model_id: str) -> None:
        models = self.load()
        if not any(m.id == model_id for m in models):
            raise KeyError(model_id)
        self.save([m for m in models if m.id != model_id])

    def get(self, model_id: str) -> Optional[LLMModelConfig]:
        return next((m for m in self.load() if m.id == model_id), None)

    def apply_validation_result(
        self,
        model_id: str,
        *,
        status: str,
        category: str | None,
        message: str,
        validated_at: str,
    ) -> LLMModelConfig:
        models = self.load()
        model = next((m for m in models if m.id == model_id), None)
        if not model:
            raise KeyError(model_id)
        model.validation_status = status
        model.validation_at = validated_at
        model.validation_category = category or ""
        model.validation_message = message
        model.updated_at = validated_at
        self.save(models)
        return model


def invalidate_ambient_models(ambient_provider: str) -> int:
    """Reset validation on ambient models for a cloud provider."""
    store = LLMModelStore()
    models = store.load()
    count = 0
    for model in models:
        if model.credential_mode != "ambient" or model.ambient_provider != ambient_provider:
            continue
        if model.validation_status != "never_validated":
            model.validation_status = "never_validated"
            model.validation_at = ""
            model.validation_category = ""
            model.validation_message = ""
            model.enabled = False
            model.default_for_agent = False
            count += 1
    if count:
        store.save(models)
    return count


def invalidate_all_model_validations() -> int:
    """Reset validation on all models; returns count updated."""
    store = LLMModelStore()
    models = store.load()
    count = 0
    for model in models:
        if model.validation_status != "never_validated":
            model.validation_status = "never_validated"
            model.validation_at = ""
            model.validation_category = ""
            model.validation_message = ""
            count += 1
    if count:
        store.save(models)
    return count
