"""Shared httpx client factory for cloud LLM catalog and validation calls."""
from __future__ import annotations

import ssl
from typing import Optional

import httpx

from ado2gh.api.connectivity_store import ConnectivityProfile, get_connectivity_profile

DEFAULT_TIMEOUT = 30.0


def _build_ssl_context(profile: ConnectivityProfile) -> ssl.SSLContext | bool:
    if not profile.custom_ca_pem.strip():
        return True
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=profile.custom_ca_pem)
    return ctx


def _proxy_url(profile: ConnectivityProfile) -> Optional[str]:
    if not profile.proxy_enabled or not profile.proxy_host:
        return None
    auth = ""
    if profile.proxy_username:
        password = profile.proxy_password or ""
        auth = f"{profile.proxy_username}:{password}@"
    return f"http://{auth}{profile.proxy_host}:{profile.proxy_port}"


def build_llm_http_client(
    *,
    for_cloud: bool,
    profile: ConnectivityProfile | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Client:
    """Build httpx client with optional proxy and custom CA for cloud outbound calls."""
    prof = profile or get_connectivity_profile()
    if not for_cloud:
        return httpx.Client(timeout=timeout)
    proxy = _proxy_url(prof)
    verify = _build_ssl_context(prof)
    return httpx.Client(timeout=timeout, proxy=proxy, verify=verify)
