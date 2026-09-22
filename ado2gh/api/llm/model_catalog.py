"""Preset and live language model catalog discovery."""
from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path
from typing import Any, Callable

import httpx

from ado2gh.api.llm.http_llm import (
    DEFAULT_TIMEOUT,
    build_cloud_llm_http_client,
    build_local_llm_http_client,
)
from ado2gh.api.llm.llm_provider_registry import PROVIDER_SPECS, get_provider_spec
from ado2gh.api.local_hosts import ollama_discovery_hint, resolve_local_service_url

_PRESETS_PATH = Path(__file__).resolve().parent / "data" / "llm_presets.json"
_catalog_lock = threading.Lock()
_catalog_inflight: dict[str, tuple[threading.Event, dict[str, Any] | None]] = {}

CACHE_KEY_CREDENTIAL_PREFIX_LENGTH = 8
"""Number of leading characters of a credential folded into the catalog cache
key, so two operators with different credentials never share a cached
catalog while the full secret still never enters the key."""

ANTHROPIC_CATALOG_PAGE_SIZE = 1000
"""Number of models requested per page when paginating the Anthropic models
catalog endpoint."""

CATALOG_SINGLE_FLIGHT_WAIT_SLACK_SECONDS = 5
"""Extra seconds a caller waits beyond the request timeout for an in-flight
catalog lookup made by another caller, so a response that lands right at the
timeout still reaches the waiting caller instead of forcing a duplicate call."""


def _load_presets() -> dict[str, list[dict[str, Any]]]:
    return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))


def _preset_entries(provider: str) -> list[dict[str, Any]]:
    """Read the bundled offline model list for a provider.

    Args:
        provider: Provider identifier to list presets for.

    Returns:
        list[dict[str, Any]]: One catalog entry per bundled model, each carrying
        the model identifier, display name, description, the requested provider
        and a source of ``preset``. Empty when the provider ships no presets.
    """
    spec = get_provider_spec(provider)
    preset_key = spec.preset_key if spec else provider
    presets = _load_presets().get(preset_key, [])
    return [
        {
            "id": item["id"],
            "display_name": item["display_name"],
            "description": item.get("description", ""),
            "provider": provider,
            "source": "preset",
        }
        for item in presets
    ]


def _catalog_key(provider: str, api_key: str, base_url: str) -> str:
    """Derive the single-flight cache key for one catalog lookup.

    Args:
        provider: Provider identifier being listed.
        api_key: Credential the lookup will use, if any. Only a short leading
            fragment is folded into the key, so that two operators using
            different credentials do not share a result.
        base_url: Endpoint the lookup will target, if any.

    Returns:
        str: An opaque key that is equal for two lookups exactly when they would
        produce the same catalog.
    """
    prefix = api_key[:CACHE_KEY_CREDENTIAL_PREFIX_LENGTH] if api_key else ""
    return f"{provider}|{prefix}|{base_url}"


def _fetch_anthropic_live(api_key: str) -> list[dict[str, Any]]:
    catalog_path = get_provider_spec("anthropic").catalog_path
    entries: list[dict[str, Any]] = []
    params: dict[str, Any] | None = {"limit": ANTHROPIC_CATALOG_PAGE_SIZE}
    with build_cloud_llm_http_client() as client:
        while params is not None:
            response = client.get(
                catalog_path,
                params=params,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            response.raise_for_status()
            data = response.json()
            for item in data.get("data", []):
                model_id = item.get("id", "")
                if not model_id:
                    continue
                entries.append(
                    {
                        "id": model_id,
                        "display_name": item.get("display_name") or model_id,
                        "description": "",
                        "provider": "anthropic",
                        "source": "live",
                    }
                )
            if not data.get("has_more"):
                break
            last_id = data.get("last_id")
            if not last_id:
                break
            params = {"limit": ANTHROPIC_CATALOG_PAGE_SIZE, "after_id": last_id}
    return entries


def _fetch_openai_live(api_key: str) -> list[dict[str, Any]]:
    with build_cloud_llm_http_client() as client:
        response = client.get(
            get_provider_spec("openai").catalog_path,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        data = response.json()
    entries = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        if not model_id:
            continue
        entries.append(
            {
                "id": model_id,
                "display_name": model_id,
                "description": "",
                "provider": "openai",
                "source": "live",
            }
        )
    return entries


def _fetch_github_models_live(api_key: str) -> list[dict[str, Any]]:
    spec = get_provider_spec("github_copilot")
    headers = {
        "Authorization": f"Bearer {api_key}",
        **(spec.runtime_headers() if spec else {}),
    }
    with build_cloud_llm_http_client() as client:
        response = client.get(spec.catalog_path if spec else "", headers=headers)
        response.raise_for_status()
        data = response.json()
    entries: list[dict[str, Any]] = []
    models = data if isinstance(data, list) else data.get("data", data.get("models", []))
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name") or ""
        if not model_id:
            continue
        entries.append(
            {
                "id": model_id,
                "display_name": item.get("display_name") or item.get("name") or model_id,
                "description": item.get("description", ""),
                "provider": "github_copilot",
                "source": "live",
            }
        )
    return entries


def _fetch_openrouter_live(api_key: str) -> list[dict[str, Any]]:
    spec = get_provider_spec("openrouter")
    headers = {
        "Authorization": f"Bearer {api_key}",
        **(spec.runtime_headers() if spec else {}),
    }
    with build_cloud_llm_http_client() as client:
        response = client.get(spec.catalog_path if spec else "", headers=headers)
        response.raise_for_status()
        data = response.json()
    entries = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        if not model_id:
            continue
        entries.append(
            {
                "id": model_id,
                "display_name": item.get("name") or model_id,
                "description": item.get("description", ""),
                "provider": "openrouter",
                "source": "live",
            }
        )
    return entries


def _live_with_preset_fallback(
    provider: str,
    *,
    api_key: str = "",
    fetcher: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Fetch a provider's live catalog, falling back to its bundled presets.

    Args:
        provider: Provider identifier being listed.
        api_key: Credential to pass to the fetcher. When empty no call is made,
            because every supported live catalog requires authentication.
        fetcher: Callable that takes the credential and returns live catalog
            entries; any failure it raises is treated as "live unavailable".

    Returns:
        dict[str, Any]: A payload with the catalog ``entries``, the ``source``
        they came from (``live`` or ``preset``) and a ``stale`` flag. Without a
        credential the entries are empty and marked live and fresh, so the UI
        prompts for one; when the live call fails or returns nothing the bundled
        presets are served and marked stale.
    """
    if not api_key:
        return {"entries": [], "source": "live", "stale": False}
    try:
        entries = fetcher(api_key)
        if entries:
            return {"entries": entries, "source": "live", "stale": False}
    except Exception:
        pass
    return {
        "entries": _preset_entries(provider),
        "source": "preset",
        "stale": True,
    }


def _ollama_headers(api_key: str = "") -> dict[str, str] | None:
    """Build the request headers for a self-hosted Ollama runtime.

    Args:
        api_key: Bearer token for runtimes that have authentication enabled.
            Ollama is commonly deployed without it.

    Returns:
        dict[str, str] | None: A single bearer authorization header when a token
        was supplied, or None so that httpx sends no authentication header.
    """
    if not api_key:
        return None
    return {"Authorization": f"Bearer {api_key}"}


def _fetch_ollama_live(base_url: str, api_key: str = "") -> list[dict[str, Any]]:
    resolved = resolve_local_service_url(base_url)
    with build_local_llm_http_client() as client:
        response = client.get(f"{resolved}/api/tags", headers=_ollama_headers(api_key))
        response.raise_for_status()
        data = response.json()
    entries = []
    for item in data.get("models", []):
        name = item.get("name", "")
        if not name:
            continue
        entries.append(
            {
                "id": name,
                "display_name": name,
                "description": "",
                "provider": "ollama",
                "source": "live",
            }
        )
    return entries


def _ollama_error_payload(base_url: str, resolved: str) -> dict[str, Any]:
    """Build the catalog payload returned when an Ollama runtime cannot be reached.

    Args:
        base_url: Base URL the operator configured.
        resolved: Base URL actually dialled after container-host rewriting.

    Returns:
        dict[str, Any]: An empty live catalog plus a ``discovery_error`` hint
        telling the operator how to make the runtime reachable, and, when the
        configured URL was rewritten, the ``resolved_base_url`` that was tried.
    """
    payload: dict[str, Any] = {
        "entries": [],
        "source": "live",
        "stale": False,
        "discovery_error": ollama_discovery_hint(base_url),
    }
    if resolved != base_url.rstrip("/"):
        payload["resolved_base_url"] = resolved
    return payload


def _ollama_catalog(base_url: str, api_key: str = "") -> dict[str, Any]:
    """List the models a self-hosted Ollama runtime currently has pulled.

    Args:
        base_url: Base URL of the runtime as configured by the operator.
        api_key: Bearer token, for runtimes with authentication enabled.

    Returns:
        dict[str, Any]: On success, the discovered ``entries`` marked live and
        fresh, plus the ``resolved_base_url`` when the configured URL had to be
        rewritten for this host. On any connection or protocol failure, an empty
        catalog carrying a ``discovery_error`` hint instead of raising, so the
        settings screen can guide the operator.
    """
    resolved = resolve_local_service_url(base_url)
    try:
        entries = _fetch_ollama_live(base_url, api_key)
        payload: dict[str, Any] = {
            "entries": entries,
            "source": "live",
            "stale": False,
        }
        if resolved != base_url.rstrip("/"):
            payload["resolved_base_url"] = resolved
        return payload
    except httpx.HTTPError:
        return _ollama_error_payload(base_url, resolved)
    except Exception:
        return _ollama_error_payload(base_url, resolved)


def _openai_catalog(provider: str, api_key: str, base_url: str) -> dict[str, Any]:
    """Catalog strategy for OpenAI: live models, falling back to bundled presets.

    Unlike the other live providers, OpenAI serves its (stale) presets even
    without an API key, instead of an empty live list; preserved as-is here.
    """
    del base_url
    if api_key:
        try:
            entries = _fetch_openai_live(api_key)
            if entries:
                return {"entries": entries, "source": "live", "stale": False}
        except Exception:
            pass
    return {
        "entries": _preset_entries(provider),
        "source": "preset",
        "stale": True,
    }


def _anthropic_catalog(provider: str, api_key: str, base_url: str) -> dict[str, Any]:
    """Catalog strategy for Anthropic: live models, falling back to bundled presets."""
    del base_url
    return _live_with_preset_fallback(provider, api_key=api_key, fetcher=_fetch_anthropic_live)


def _github_copilot_catalog(provider: str, api_key: str, base_url: str) -> dict[str, Any]:
    """Catalog strategy for GitHub Copilot / Models: live, falling back to presets."""
    del base_url
    return _live_with_preset_fallback(provider, api_key=api_key, fetcher=_fetch_github_models_live)


def _openrouter_catalog(provider: str, api_key: str, base_url: str) -> dict[str, Any]:
    """Catalog strategy for OpenRouter: live models, falling back to bundled presets."""
    del base_url
    return _live_with_preset_fallback(provider, api_key=api_key, fetcher=_fetch_openrouter_live)


def _preset_only_catalog(provider: str, api_key: str, base_url: str) -> dict[str, Any]:
    """Catalog strategy for providers with no queryable catalog: bundled presets only."""
    del api_key, base_url
    return {
        "entries": _preset_entries(provider),
        "source": "preset",
        "stale": False,
    }


def _ollama_dispatch_catalog(provider: str, api_key: str, base_url: str) -> dict[str, Any]:
    """Catalog strategy for self-hosted Ollama: live models from the configured runtime."""
    del provider
    if not base_url:
        raise ValueError("base_url required for ollama provider")
    return _ollama_catalog(base_url, api_key)


_CATALOG_FETCHERS: dict[str, Callable[[str, str, str], dict[str, Any]]] = {
    "openai": _openai_catalog,
    "anthropic": _anthropic_catalog,
    "github_copilot": _github_copilot_catalog,
    "openrouter": _openrouter_catalog,
    "azure_openai": _preset_only_catalog,
    "google_gemini": _preset_only_catalog,
    "ollama": _ollama_dispatch_catalog,
}
"""Catalog strategy per provider, wired onto each ``LLMProviderSpec`` below.

This is the single place a new provider's catalog behaviour is registered:
adding a provider means adding one entry here (and, if it needs its own
strategy, one small adapter function above), never a new branch in
``_catalog_result``.
"""

for _provider_id, _fetcher in _CATALOG_FETCHERS.items():
    _spec = get_provider_spec(_provider_id)
    if _spec is not None:
        PROVIDER_SPECS[_provider_id] = dataclasses.replace(_spec, catalog_fetcher=_fetcher)


def _catalog_result(provider: str, *, api_key: str = "", base_url: str = "") -> dict[str, Any]:
    """Produce the catalog for one provider using that provider's registered strategy.

    Args:
        provider: Provider identifier to list models for.
        api_key: Credential to use for providers whose catalog is authenticated.
        base_url: Endpoint to query, required by self-hosted providers.

    Returns:
        dict[str, Any]: A payload with ``entries``, the ``source`` they came
        from and a ``stale`` flag. Providers with a queryable catalog are
        listed live and fall back to bundled presets; providers without one are
        served from presets directly.

    Raises:
        ValueError: If a self-hosted provider was requested without a base URL,
            or the provider has no catalog strategy at all.
    """
    spec = get_provider_spec(provider)
    if spec is None or spec.catalog_fetcher is None:
        raise ValueError(f"Unsupported catalog provider: {provider}")
    return spec.catalog_fetcher(provider, api_key, base_url)


def list_catalog(
    *,
    provider: str,
    api_key: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    """Return catalog entries with live-primary, preset-fallback semantics."""
    key = _catalog_key(provider, api_key, base_url)
    with _catalog_lock:
        if key in _catalog_inflight:
            event, _ = _catalog_inflight[key]
            waiting = True
        else:
            event = threading.Event()
            _catalog_inflight[key] = (event, None)
            waiting = False

    if waiting:
        event.wait(timeout=DEFAULT_TIMEOUT + CATALOG_SINGLE_FLIGHT_WAIT_SLACK_SECONDS)
        with _catalog_lock:
            slot = _catalog_inflight.get(key)
            if slot and slot[1] is not None:
                return dict(slot[1])
        return _catalog_result(provider, api_key=api_key, base_url=base_url)

    try:
        result = _catalog_result(provider, api_key=api_key, base_url=base_url)
    except Exception:
        with _catalog_lock:
            _catalog_inflight.pop(key, None)
            event.set()
        raise

    with _catalog_lock:
        _catalog_inflight[key] = (event, result)
        event.set()
        _catalog_inflight.pop(key, None)
    return result
