"""Tests for cloud credential live probes (007)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ado2gh.api.credentials.cloud_credential_probe import probe_provider
from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialSource


def test_probe_aws_passed(monkeypatch):
    pytest.importorskip("boto3")  # optional AWS extra; patch("boto3.client") needs it importable
    monkeypatch.setenv("ADO2GH_BEDROCK_MODEL_ID", "anthropic.claude-3")
    source = CloudCredentialSource(provider="aws", region="us-east-1")
    mock_client = MagicMock()
    with patch("boto3.client", return_value=mock_client):
        result = probe_provider("aws", source)
    assert result["status"] == "passed"
    mock_client.converse.assert_called_once()


def test_probe_unknown_provider():
    source = CloudCredentialSource(provider="aws")
    result = probe_provider("unknown", source)
    assert result["status"] == "failed"


# ── GAP-057: the Vertex probe could never report success ──────────────────────
#
# `_probe_gcp` called `google.auth.transport.requests.Request()` after importing
# only `google.auth`. A submodule is an attribute of its package only once it has
# been imported, so that expression raised AttributeError, the `except Exception`
# at the end of the probe swallowed it, and every Vertex probe reported a failure
# no matter how good the host's credentials were. `d1427fd` (T077) added the
# missing `import google.auth.transport.requests` while making mypy a CI gate;
# these tests cover that fix, which landed with no test of its own.
#
# Deliberately *not* patched: `google.auth.transport.requests.Request`. Patching
# it by name would import the submodule itself and paper over the very defect
# under test, so the real Request object is constructed (no network on
# construction) and only `google.auth.default` and `httpx.Client` are faked.


def _fake_gcp_credentials():
    creds = MagicMock()
    creds.token = "fake-gcp-access-token"  # obvious fake (CA-003)
    return creds


def _fake_httpx_client(status_code):
    client = MagicMock()
    client.__enter__.return_value.post.return_value.status_code = status_code
    return MagicMock(return_value=client)


def _probe_vertex(monkeypatch, status_code):
    pytest.importorskip("google.auth", reason="google-auth is not installed in this environment")
    monkeypatch.setenv("ADO2GH_VERTEX_MODEL_ID", "gemini-fake")
    source = CloudCredentialSource(provider="gcp", project="fake-project", region="us-central1")
    with patch("google.auth.default", return_value=(_fake_gcp_credentials(), None)), \
            patch("httpx.Client", _fake_httpx_client(status_code)):
        return probe_provider("gcp", source)


def test_probe_vertex_passed(monkeypatch):
    result = _probe_vertex(monkeypatch, 200)
    assert result["status"] == "passed", result["message"]
    assert result["category"] is None


def test_probe_vertex_rejected_credentials(monkeypatch):
    result = _probe_vertex(monkeypatch, 403)
    assert result["status"] == "failed"
    assert result["category"] == "credentials"


def test_probe_vertex_requires_project_and_model(monkeypatch):
    monkeypatch.delenv("ADO2GH_VERTEX_MODEL_ID", raising=False)
    result = probe_provider("gcp", CloudCredentialSource(provider="gcp", project="fake-project"))
    assert result["status"] == "failed"
    assert result["category"] == "credentials"
