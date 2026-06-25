"""Tests for LLM model store validation gates and platform-managed models."""
import pytest

from ado2gh.api.llm.llm_model_store import LLMModelStore


def test_enable_gate_rejects_without_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = LLMModelStore()
    model = store.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
        }
    )
    with pytest.raises(ValueError, match="validation"):
        store.upsert({"enabled": True}, model_id=model.id)


def test_enable_allowed_after_validation_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = LLMModelStore()
    model = store.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
        }
    )
    store.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    updated = store.upsert({"enabled": True}, model_id=model.id)
    assert updated.enabled is True


def test_sensitive_change_resets_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = LLMModelStore()
    model = store.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
        }
    )
    store.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    updated = store.upsert({"model_id": "gpt-4o"}, model_id=model.id)
    assert updated.validation_status == "never_validated"


def test_override_allowed_when_toggle_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from ado2gh.api.connectivity_store import ConnectivityStore

    ConnectivityStore().update({"allow_custom_model_id": True}, actor="admin")
    store = LLMModelStore()
    model = store.upsert(
        {
            "display_name": "Custom",
            "provider": "openai",
            "model_id": "custom-model",
            "api_key": "sk-test",
            "catalog_source": "override",
        }
    )
    assert model.catalog_source == "override"


def test_override_rejected_when_toggle_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = LLMModelStore()
    with pytest.raises(ValueError, match="override"):
        store.upsert(
            {
                "display_name": "Custom",
                "provider": "openai",
                "model_id": "custom-model",
                "api_key": "sk-test",
                "catalog_source": "override",
            }
        )


def test_ambient_enable_requires_approval(tmp_path, monkeypatch):
    """Platform-supplied ambient models require explicit approval before enabling."""
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = LLMModelStore()
    model = store.upsert(
        {
            "id": "platform-bedrock",
            "display_name": "Platform",
            "provider": "bedrock",
            "model_id": "anthropic.claude-3",
            "credential_mode": "ambient",
            "platform_supplied": True,
            "ambient_provider": "aws",
        }
    )
    store.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    with pytest.raises(ValueError, match="approved"):
        store.upsert({"enabled": True}, model_id=model.id)
