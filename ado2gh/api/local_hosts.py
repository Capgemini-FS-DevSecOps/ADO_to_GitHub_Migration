"""Resolve localhost URLs for server-side calls to services on the host machine."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse


def resolve_local_service_url(base_url: str) -> str:
    """Rewrite localhost/127.0.0.1 to a host-reachable alias when running in Docker."""
    raw = (base_url or "").strip()
    if not raw:
        return raw
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        return raw.rstrip("/")

    alias = os.environ.get("ADO2GH_LOCAL_HOST_ALIAS", "").strip()
    if not alias and Path("/.dockerenv").exists():
        alias = "host.docker.internal"
    if not alias:
        return raw.rstrip("/")

    port = parsed.port
    scheme = parsed.scheme or "http"
    netloc = f"{alias}:{port}" if port else alias
    return urlunparse((scheme, netloc, "", "", "", "")).rstrip("/")


def ollama_discovery_hint(base_url: str) -> str:
    """User-facing hint when Ollama discovery fails from a containerized API."""
    resolved = resolve_local_service_url(base_url)
    if resolved != base_url.rstrip("/"):
        return (
            f"Could not reach Ollama at {base_url}. "
            f"When the API runs in Docker, use {resolved} or set ADO2GH_LOCAL_HOST_ALIAS."
        )
    return (
        f"Could not reach Ollama at {base_url}. "
        "Ensure Ollama is running (ollama serve) and the base URL is reachable from the API host."
    )
