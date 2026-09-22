"""Shared httpx client factory for cloud language model catalog and validation calls."""
from __future__ import annotations

import ssl
from typing import Optional

import httpx

from ado2gh.api.connectivity_store import ConnectivityProfile, get_connectivity_profile

DEFAULT_TIMEOUT = 30.0


def _build_ssl_context(profile: ConnectivityProfile) -> ssl.SSLContext | bool:
    """Build the TLS verification setting for outbound calls.

    Args:
        profile: Connectivity profile that may carry an enterprise CA bundle.

    Returns:
        ssl.SSLContext | bool: An SSL context preloaded with the profile's custom
        CA bundle when one is configured, otherwise ``True`` to keep httpx's
        default system trust store.
    """
    if not profile.custom_ca_pem.strip():
        return True
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=profile.custom_ca_pem)
    return ctx


def _proxy_url(profile: ConnectivityProfile) -> Optional[str]:
    """Assemble the outbound proxy URL described by a connectivity profile.

    Args:
        profile: Connectivity profile holding the proxy host, port and optional
            proxy credentials.

    Returns:
        Optional[str]: A proxy URL with the profile's host and port, carrying
        userinfo credentials when the profile supplies a proxy username, or
        None when proxying is disabled or no host is configured.
    """
    if not profile.proxy_enabled or not profile.proxy_host:
        return None
    auth = ""
    if profile.proxy_username:
        password = profile.proxy_password or ""
        auth = f"{profile.proxy_username}:{password}@"
    return f"http://{auth}{profile.proxy_host}:{profile.proxy_port}"


def build_cloud_llm_http_client(
    *,
    profile: ConnectivityProfile | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Client:
    """Build an httpx client for calls that leave the network perimeter.

    The client honours the platform connectivity profile: it routes through the
    configured egress proxy and trusts the configured enterprise CA bundle.

    Args:
        profile: Connectivity profile to apply. Defaults to the stored platform
            profile when omitted.
        timeout: Per-request timeout in seconds.

    Returns:
        httpx.Client: An unopened client configured with the profile's proxy and
        TLS trust settings, intended for use as a context manager.
    """
    prof = profile or get_connectivity_profile()
    return httpx.Client(timeout=timeout, proxy=_proxy_url(prof), verify=_build_ssl_context(prof))


def build_local_llm_http_client(*, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Build an httpx client for calls to a self-hosted runtime on the local network.

    Egress proxy and custom CA settings are deliberately skipped: they apply to
    internet-bound traffic and would break loopback or in-cluster addresses.

    Args:
        timeout: Per-request timeout in seconds.

    Returns:
        httpx.Client: An unopened client with default trust settings and no
        proxy, intended for use as a context manager.
    """
    return httpx.Client(timeout=timeout)
