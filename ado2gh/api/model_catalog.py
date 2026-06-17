"""Preset and live LLM model catalog discovery."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ado2gh.api.http_llm import DEFAULT_TIMEOUT, build_llm_http_client

_PRESETS_PATH = Path(__file__).resolve().parent / "data" / "llm_presets.json"
_catalog_lock = threading.Lock()
_catalog_inflight: dict[str, tuple[threading.Event, dict[str, Any] | None]] = {}


def _load_presets() -> dict[str, list[dict[str, Any]]]:
    return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))


def _preset_entries(provider: str) -> list[dict[str, Any]]:
    presets = _load_presets().get(provider, [])
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


def _fetch_ollama_live(base_url: str) -> list[dict[str, Any]]:
    base = base_url.rstrip("/")
    with build_llm_http_client(for_cloud=False) as client:
        response = client.get(f"{base}/api/tags")
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


def _catalog_result(provider: str, *, api_key: str = "", base_url: str = "") -> dict[str, Any]:
    if provider == "anthropic":
        return {
            "entries": _preset_entries("anthropic"),
            "source": "preset",
            "stale": False,
        }
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
    if provider == "ollama":
        if not base_url:
            raise ValueError("base_url required for ollama provider")
        entries = _fetch_ollama_live(base_url)
        return {"entries": entries, "source": "live", "stale": False}
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
