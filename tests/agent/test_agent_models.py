"""Tests for agent model list (007)."""
from __future__ import annotations

from ado2gh.api.agent_models import list_agent_models


def test_ambient_hidden_when_not_approved(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADO2GH_BEDROCK_MODEL_ID", "anthropic.claude-3")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    from ado2gh.api.llm.platform_managed_model import sync_on_startup
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    sync_on_startup()
    store = LLMModelStore()
    model = store.get("platform-bedrock")
    store.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    assert list_agent_models()["models"] == []

    from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore

    creds = CloudCredentialsStore()
    creds.apply_scan(
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
    creds.approve("aws", actor="admin", probe_fn=lambda *_: {"status": "passed"})
    store.upsert({"enabled": True}, model_id=model.id)
    listed = list_agent_models()
    assert len(listed["models"]) == 1
    assert listed["models"][0]["credential_mode"] == "ambient"
    creds.revoke("aws", actor="admin")
    assert list_agent_models()["models"] == []
