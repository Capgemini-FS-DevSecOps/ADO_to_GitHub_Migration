"""Admin-managed language model provider configurations (secrets stored locally, never returned)."""
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
    """Resolve the on-disk location of the language model configuration file.

    Returns:
        Path: The ``llm_models.json`` path under the configured platform data
        directory, falling back to the current working directory when that
        environment variable is unset.
    """
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "llm_models.json"


@dataclass
class LLMModelConfig:
    """One admin-configured language model and its validation state.

    Holds the provider routing details, the operator-facing labels, the
    validation verdict that gates enabling the model, and the credential mode
    that decides whether a stored key or ambient cloud credentials are used.
    The ``api_key`` attribute holds the live secret in memory and is never
    included in any operator-facing payload.
    """

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
        """Render the model in the redacted shape returned to operators and stored on disk.

        Returns:
            dict[str, Any]: Every configuration field of the model — identity,
            provider routing, catalog provenance, enable and default flags,
            credential mode, ambient source and agent capabilities — with
            optional string fields normalised to None when empty. The API key is
            replaced by a fixed mask when a key is stored and by an empty string
            when none is; the stored key itself is never included.
        """
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
    """Decide whether an update invalidates the model's previous validation verdict.

    Args:
        existing: The stored model configuration before the update.
        data: Partial update payload from the operator.

    Returns:
        bool: True when the update alters something the last validation depended
        on — the provider, the model identifier, the base URL, the catalog
        source, or the credential itself — so the verdict must be discarded.
        A masked credential placeholder means "unchanged" and does not count.
    """
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
    """Check that a model relying on ambient cloud credentials may use them.

    Args:
        model: The model configuration to check.

    Returns:
        bool: True when the model does not use ambient credentials at all, or
        when the cloud credential source it names has been approved by an
        administrator; False while that source is still unapproved.
    """
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
    """JSON-backed store of admin-configured language models.

    Reads and writes the whole model list on every operation, so each call sees
    the current file contents. Stored credentials are kept in a separate section
    of the file and are only ever exposed through the in-memory
    :class:`LLMModelConfig` objects, never through the redacted public payloads.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Bind the store to a configuration file.

        Args:
            path: File to read and write. When omitted the platform data
                directory location is resolved on every access, not here: the
                route layer keeps one store for the process lifetime
                (``services/accelerator_api/routes/_shared.py``), so resolving
                it in the constructor froze it at import time and every later
                change to ``ADO2GH_DATA_DIR`` was ignored.
        """
        self._path = path

    @property
    def path(self) -> Path:
        """Configuration file this store reads and writes, resolved on every access.

        Returns:
            Path: The explicit path this store was constructed with, or the
            current location under the platform data directory.
        """
        return self._path or _path()

    def load(self) -> list[LLMModelConfig]:
        """Read every configured model from disk.

        Returns:
            list[LLMModelConfig]: One configuration per stored model, each with
            its stored credential reattached, in file order. Empty when the
            store file does not exist yet.
        """
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        secrets = data.get("_secrets", {})
        return [_parse_model(raw, secrets) for raw in data.get("models", [])]

    def save(self, models: list[LLMModelConfig]) -> None:
        """Replace the stored model list with the supplied one.

        Model records are written in their redacted public form, and the
        credentials they carry are written to a separate section of the same
        file so that reading the record list never discloses them. Creates the
        parent directory when it is missing.

        Args:
            models: The complete model list to persist; anything previously in
                the file and absent here is dropped.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        secrets = {m.id: m.api_key for m in models if m.api_key}
        public = [m.to_public() for m in models]
        payload = {"models": public, "_secrets": secrets}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_public(self) -> list[dict[str, Any]]:
        """List the enabled models in their redacted operator-facing form.

        Returns:
            list[dict[str, Any]]: One redacted model payload per enabled model.
            Disabled models are omitted, and no stored credential is included.
        """
        return [m.to_public() for m in self.load() if m.enabled]

    def list_agent_ready_models(self) -> list[LLMModelConfig]:
        """List the models the migration agent is currently allowed to run on.

        Returns:
            list[LLMModelConfig]: The enabled models that have cleared every
            gate — offline and stub providers qualify immediately, cloud
            providers must have passed validation, and models backed by ambient
            cloud credentials also need their credential source approved.
        """
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
        """Report whether the agent has at least one usable model.

        Returns:
            bool: True when any configured model has cleared the enable,
            validation and credential-approval gates.
        """
        return bool(self.list_agent_ready_models())

    def get_default_model(self) -> Optional[LLMModelConfig]:
        """Pick the model the agent should use when the caller names none.

        Returns:
            Optional[LLMModelConfig]: The model flagged as the agent default,
            provided it is enabled and either offline or validated. When that
            flag points at an unusable model, the first enabled non-stub model
            that passed validation is used instead. None when nothing qualifies.
        """
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
        """Create a model, or apply a partial update to an existing one.

        Updating a field the last validation depended on resets that model's
        validation state, so it must be revalidated before it can be enabled
        again. Setting a model as the agent default clears the flag on all
        others. Platform-supplied models accept only a display-name change.

        Args:
            data: Model fields to write. On create, a display name is required
                and the remaining fields fall back to provider defaults; on
                update, only the keys present are applied. A masked credential
                placeholder leaves the stored credential untouched.
            model_id: Identifier of the model to update. When omitted a new
                model is created and assigned a generated identifier unless the
                payload supplies one.

        Returns:
            LLMModelConfig: The created or updated model as persisted, including
            refreshed timestamps and any reset validation state.

        Raises:
            KeyError: If model_id is given but no such model exists.
            ValueError: If a credential is required for the provider and none
                was supplied, if a custom model identifier is used while that
                override is disabled, if read-only fields of a platform-supplied
                model are changed, if the model is reported as unable to call
                tools, or if enabling is attempted before validation passed or
                before ambient credentials were approved.
        """
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
            # Reject registration when the model does not support tool calling; the agent requires it.
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
        """Remove a model and its stored credential from the store.

        Args:
            model_id: Identifier of the model to remove.

        Raises:
            KeyError: If no model with that identifier exists.
        """
        models = self.load()
        if not any(m.id == model_id for m in models):
            raise KeyError(model_id)
        self.save([m for m in models if m.id != model_id])

    def get(self, model_id: str) -> Optional[LLMModelConfig]:
        """Fetch a single model configuration by identifier.

        Args:
            model_id: Identifier of the model to fetch.

        Returns:
            Optional[LLMModelConfig]: The stored configuration with its
            credential attached, or None when no model matches.
        """
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
        """Record the outcome of a connectivity check against a stored model.

        Args:
            model_id: Identifier of the model that was validated.
            status: Verdict to record, one of the supported validation statuses.
            category: Failure category for a failed verdict, or None when the
                check passed.
            message: Operator-facing explanation of the verdict.
            validated_at: Timestamp of the check, also used as the model's new
                update time.

        Returns:
            LLMModelConfig: The model with its validation verdict, category,
            message and timestamps updated and persisted.

        Raises:
            KeyError: If no model with that identifier exists.
        """
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
    """Reset validation on ambient models for a cloud provider.

    Args:
        ambient_provider: Cloud provider whose ambient-credentialed models
            (``credential_mode == "ambient"``) should be invalidated.

    Returns:
        The number of matching models whose validation state was actually
        reset (already-``never_validated`` models are skipped and not
        counted).
    """
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
