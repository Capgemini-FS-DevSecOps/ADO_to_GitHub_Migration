"""Tests for environment connectivity profile store."""
from pathlib import Path

from ado2gh.api.connectivity_store import ConnectivityProfile, ConnectivityStore
from ado2gh.api.llm.llm_model_store import LLMModelStore


def test_the_data_directory_is_resolved_per_access_not_at_construction(tmp_path, monkeypatch):
    """The route layer keeps one store for the process lifetime, so a frozen path leaks state.

    ``services/accelerator_api/routes/_shared.py`` builds its ``ConnectivityStore`` at import.
    While the constructor resolved ``ADO2GH_DATA_DIR`` eagerly, every later change to the
    variable was ignored by anything reached through a route: one deployment's profile — and
    one test's — was read and written for every other.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(first))
    store = ConnectivityStore()
    store.update({"proxy_host": "first.example"}, actor="admin")

    monkeypatch.setenv("ADO2GH_DATA_DIR", str(second))

    assert store.path.parent == second
    assert store.load().proxy_host == "", "the profile from the old directory must not leak"
    store.update({"proxy_host": "second.example"}, actor="admin")
    assert (second / "connectivity_profile.json").exists()
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(first))
    assert store.load().proxy_host == "first.example"


def test_an_explicit_path_still_wins_over_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path / "ignored"))
    explicit: Path = tmp_path / "explicit.json"

    store = ConnectivityStore(path=explicit)
    store.update({"proxy_host": "pinned.example"}, actor="admin")

    assert store.path == explicit
    assert explicit.exists()


def test_connectivity_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = ConnectivityStore()
    profile = store.load()
    assert profile.proxy_enabled is False
    assert profile.allow_custom_model_id is False
    assert profile.custom_ca_configured is False


def test_connectivity_masks_secrets_in_public(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = ConnectivityStore()
    profile = ConnectivityProfile(
        proxy_enabled=True,
        proxy_host="proxy.corp",
        proxy_port=8080,
        proxy_password="secret-pass",
        custom_ca_pem="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
    )
    store.save(profile, actor="admin")
    public = store.load().to_public()
    assert public["proxy_password"] == "***"
    assert public["custom_ca_configured"] is True
    assert "BEGIN CERTIFICATE" not in str(public)


def test_connectivity_update_retains_masked_password(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = ConnectivityStore()
    store.update({"proxy_password": "keep-me"}, actor="admin")
    store.update({"proxy_enabled": True, "proxy_password": "***"}, actor="admin")
    loaded = store.load()
    assert loaded.proxy_password == "keep-me"


def test_connectivity_change_invalidates_model_validations(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    models = LLMModelStore()
    model = models.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
        }
    )
    models.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    ConnectivityStore().update({"proxy_enabled": True, "proxy_host": "proxy"}, actor="admin")
    reloaded = models.get(model.id)
    assert reloaded.validation_status == "never_validated"


def test_connectivity_clear_custom_ca(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    store = ConnectivityStore()
    store.update({"custom_ca_pem": "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----"}, actor="admin")
    assert store.load().custom_ca_configured is True
    store.update({"custom_ca_pem": ""}, actor="admin")
    assert store.load().custom_ca_configured is False


def test_allow_custom_model_id_toggle_invalidates_validations(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    models = LLMModelStore()
    model = models.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test",
        }
    )
    models.apply_validation_result(
        model.id,
        status="passed",
        category=None,
        message="ok",
        validated_at="2026-06-16T00:00:00Z",
    )
    ConnectivityStore().update({"allow_custom_model_id": True}, actor="admin")
    assert models.get(model.id).validation_status == "never_validated"
