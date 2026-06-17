"""Tests for extended LLM model store validation gate (006)."""
import pytest

from ado2gh.api.llm_model_store import LLMModelStore


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
