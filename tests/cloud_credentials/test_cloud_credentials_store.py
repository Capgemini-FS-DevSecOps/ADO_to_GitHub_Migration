"""Tests for cloud credential store (007)."""
from __future__ import annotations

import json

import pytest

from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore, ScanInFlightError


def test_scan_persists_aws_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
    store = CloudCredentialsStore()
    sources = store.scan()
    aws = next(s for s in sources if s.provider == "aws")
    assert aws.completeness == "complete"
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    blob = json.dumps(payload)
    assert "supersecret" not in blob
    assert "AWS_SECRET_ACCESS_KEY" not in blob


def test_approve_requires_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = CloudCredentialsStore()
    store.apply_scan(
        [
            {
                "provider": "aws",
                "completeness": "incomplete",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": ["AWS_REGION"],
            },
            {
                "provider": "foundry",
                "completeness": "absent",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
            {
                "provider": "gcp",
                "completeness": "absent",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
        ]
    )
    with pytest.raises(ValueError, match="incomplete_configuration"):
        store.approve("aws", actor="admin", probe_fn=lambda *_: {"status": "passed"})


def test_approve_and_revoke_invalidate_ambient(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADO2GH_BEDROCK_MODEL_ID", "anthropic.claude-3")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    from ado2gh.api.llm.platform_managed_model import sync_on_startup
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    sync_on_startup()
    store = CloudCredentialsStore()
    store.apply_scan(
        [
            {
                "provider": "aws",
                "completeness": "complete",
                "primary_method": "env_keys",
                "alternate_methods": [],
                "region": "us-east-1",
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
            {
                "provider": "foundry",
                "completeness": "absent",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
            {
                "provider": "gcp",
                "completeness": "absent",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
        ]
    )
    store.approve("aws", actor="admin", probe_fn=lambda *_: {"status": "passed"})
    model_store = LLMModelStore()
    model = model_store.get("platform-bedrock")
    model_store.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    model_store.upsert({"enabled": True}, model_id=model.id)
    store.revoke("aws", actor="admin")
    refreshed = model_store.get(model.id)
    assert refreshed.enabled is False
    assert refreshed.validation_status == "never_validated"


def test_patch_rejects_secret_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = CloudCredentialsStore()
    store.apply_scan(
        [
            {
                "provider": "aws",
                "completeness": "incomplete",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": ["AWS_REGION"],
            },
            {
                "provider": "foundry",
                "completeness": "absent",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
            {
                "provider": "gcp",
                "completeness": "absent",
                "primary_method": "",
                "alternate_methods": [],
                "region": None,
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            },
        ]
    )
    with pytest.raises(ValueError, match="invalid_field"):
        store.patch_provider("aws", {"api_key": "nope"})


def test_scan_single_flight(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = CloudCredentialsStore()
    import ado2gh.api.credentials.cloud_credentials_store as mod

    mod._scan_lock.acquire()
    try:
        mod._scan_inflight = True
        with pytest.raises(ScanInFlightError):
            store.scan()
    finally:
        mod._scan_inflight = False
        mod._scan_lock.release()
