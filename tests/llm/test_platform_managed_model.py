"""Tests for platform-supplied model sync (007)."""
from __future__ import annotations

from ado2gh.api.llm.platform_managed_model import read_platform_config, sync_on_startup


def test_sync_creates_ambient_bedrock_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADO2GH_BEDROCK_MODEL_ID", "anthropic.claude-3")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    cfg = read_platform_config()
    assert cfg is not None
    assert cfg.provider == "bedrock"
    sync_on_startup()
    from ado2gh.api.llm.llm_model_store import LLMModelStore

    model = LLMModelStore().get("platform-bedrock")
    assert model is not None
    assert model.platform_supplied is True
    assert model.credential_mode == "ambient"
    assert model.enabled is False
