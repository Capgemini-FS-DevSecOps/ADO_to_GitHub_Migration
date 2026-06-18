"""Preset and live LLM model catalog discovery."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import httpx

from ado2gh.api.http_llm import DEFAULT_TIMEOUT, build_llm_http_client
from ado2gh.api.llm_provider_registry import get_provider_spec
from ado2gh.api.local_hosts import ollama_discovery_hint, resolve_local_service_url

_PRESETS_PATH = Path(__file__).resolve().parent / "data" / "llm_presets.json"
_catalog_lock = threading.Lock()
_catalog_inflight: dict[str, tuple[threading.Event, dict[str, Any] | None]] = {}


def _load_presets() -> dict[str, list[dict[str, Any]]]:
    return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))


def _preset_entries(provider: str) -> list[dict[str, Any]]:
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
    return f"{provider}|{api_key[:8] if api_key else ''}|{base_url}"


def _fetch_anthropic_live(api_key: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    params: dict[str, Any] | None = {"limit": 1000}
    with build_llm_http_client(for_cloud=True) as client:
        while params is not None:
            response = client.get(
                "https://api.anthropic.com/v1/models",
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
            params = {"limit": 1000, "after_id": last_id}
    return entries


def _fetch_openai_live(api_key: str) -> list[dict[str, Any]]:
    with build_llm_http_client(for_cloud=True) as client:
        response = client.get(
            "https://api.openai.com/v1/models",
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
    with build_llm_http_client(for_cloud=True) as client:
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
    with build_llm_http_client(for_cloud=True) as client:
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
    fetcher,
) -> dict[str, Any]:
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
    if not api_key:
        return None
    return {"Authorization": f"Bearer {api_key}"}


def _fetch_ollama_live(base_url: str, api_key: str = "") -> list[dict[str, Any]]:
    resolved = resolve_local_service_url(base_url)
    with build_llm_http_client(for_cloud=False) as client:
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


def _catalog_result(provider: str, *, api_key: str = "", base_url: str = "") -> dict[str, Any]:
    if provider == "anthropic":
        return _live_with_preset_fallback(
            provider, api_key=api_key, fetcher=_fetch_anthropic_live,
        )
    if provider == "openai":
        if api_key:
            try:
                entries = _fetch_openai_live(api_key)
                if entries:
                    return {"entries": entries, "source": "live", "stale": False}
            except Exception:
                pass
        return {
            "entries": _preset_entries("openai"),
            "source": "preset",
            "stale": True,
        }
    if provider == "github_copilot":
        return _live_with_preset_fallback(
            provider, api_key=api_key, fetcher=_fetch_github_models_live,
        )
    if provider == "openrouter":
        return _live_with_preset_fallback(
            provider, api_key=api_key, fetcher=_fetch_openrouter_live,
        )
    if provider in ("azure_openai", "google_gemini"):
        return {
            "entries": _preset_entries(provider),
            "source": "preset",
            "stale": False,
        }
    if provider == "ollama":
        if not base_url:
            raise ValueError("base_url required for ollama provider")
        return _ollama_catalog(base_url, api_key)
    raise ValueError(f"Unsupported catalog provider: {provider}")


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
        event.wait(timeout=DEFAULT_TIMEOUT + 5)
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
