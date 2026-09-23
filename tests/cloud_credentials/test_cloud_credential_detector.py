"""Tests for cloud credential presence detector (007)."""
from __future__ import annotations

from ado2gh.api.credentials.cloud_credential_detector import scan_all_presence


def test_aws_complete_with_env_keys(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(
        "ado2gh.api.credentials.cloud_credential_detector._imds_reachable",
        lambda: False,
    )
    aws = next(d for d in scan_all_presence() if d["provider"] == "aws")
    assert aws["completeness"] == "complete"
    assert aws["primary_method"] == "env_keys"


def test_foundry_incomplete_without_endpoint(monkeypatch):
    monkeypatch.delenv("ADO2GH_FOUNDRY_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    foundry = next(d for d in scan_all_presence() if d["provider"] == "foundry")
    assert foundry["completeness"] in ("absent", "incomplete")
