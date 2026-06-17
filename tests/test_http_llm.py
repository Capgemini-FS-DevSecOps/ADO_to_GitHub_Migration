"""Tests for shared LLM httpx client factory."""
from unittest.mock import patch

import ssl

from ado2gh.api.connectivity_store import ConnectivityProfile
from ado2gh.api.http_llm import build_llm_http_client


def test_local_client_skips_proxy():
    client = build_llm_http_client(for_cloud=False)
    try:
        assert client.timeout.connect == 30.0
    finally:
        client.close()


def test_cloud_client_uses_proxy():
    profile = ConnectivityProfile(
        proxy_enabled=True,
        proxy_host="proxy.local",
        proxy_port=8080,
        proxy_username="user",
        proxy_password="pass",
    )
    with patch("ado2gh.api.http_llm.httpx.Client") as mock_client:
        build_llm_http_client(for_cloud=True, profile=profile)
        kwargs = mock_client.call_args.kwargs
        assert kwargs["proxy"] == "http://user:pass@proxy.local:8080"


def test_cloud_client_loads_custom_ca():
    profile = ConnectivityProfile(
        custom_ca_pem="-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----",
    )
    fake_ctx = ssl.create_default_context()
    with patch("ado2gh.api.http_llm.ssl.create_default_context", return_value=fake_ctx):
        with patch.object(fake_ctx, "load_verify_locations") as load_ca:
            with patch("ado2gh.api.http_llm.httpx.Client"):
                build_llm_http_client(for_cloud=True, profile=profile)
            load_ca.assert_called_once()
