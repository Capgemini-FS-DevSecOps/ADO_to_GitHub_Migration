"""Tests for cloud credential live probes (007)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.api.credentials.cloud_credential_probe import probe_provider
from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialSource


def test_probe_aws_passed(monkeypatch):
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
