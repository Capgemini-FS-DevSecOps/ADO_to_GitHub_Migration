"""Every JSON-backed settings store must follow ``ADO2GH_DATA_DIR`` as it is now.

``services/accelerator_api/routes/_shared.py`` builds one ``SettingsStore``, one
``LLMModelStore``, one ``CloudCredentialsStore`` and one ``ConnectivityStore`` at module
import and keeps them for the process lifetime. While those constructors resolved
``ADO2GH_DATA_DIR`` eagerly, every later change to the variable was ignored by anything
reached through a route: whichever directory happened to be current at import time was
read and written for every request after it — leaking one deployment's profile, and one
test's, into the next.

The connectivity store's own version of this lives in
``tests/core/test_connectivity_store.py``; these three cover its siblings.
"""
from __future__ import annotations

from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore
from ado2gh.api.llm.llm_model_store import LLMModelStore
from ado2gh.api.settings_store import SettingsStore

FAKE_KEY = "sk-fake-not-a-real-key-0003"


def test_settings_store_resolves_the_data_directory_per_access(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(first))
    store = SettingsStore()
    store.save(store.load())
    assert (first / "ui_settings.json").exists()

    monkeypatch.setenv("ADO2GH_DATA_DIR", str(second))

    assert store.path.parent == second
    store.save(store.load())
    assert (second / "ui_settings.json").exists(), "the store must write where the variable now points"


def test_settings_store_still_accepts_a_pinned_path(tmp_path, monkeypatch):
    """Two existing tests assign ``_settings.path`` directly; that must keep working."""
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path / "ignored"))
    pinned = tmp_path / "pinned_settings.json"
    store = SettingsStore()

    store.path = pinned
    store.save(store.load())

    assert store.path == pinned
    assert pinned.exists()
    assert not (tmp_path / "ignored" / "ui_settings.json").exists()


def test_llm_model_store_resolves_the_data_directory_per_access(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(first))
    store = LLMModelStore()
    store.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": FAKE_KEY,
        }
    )
    assert len(store.load()) == 1

    monkeypatch.setenv("ADO2GH_DATA_DIR", str(second))

    assert store.path.parent == second
    assert store.load() == [], "a model configured in the old directory must not leak into the new one"


def test_llm_model_store_still_accepts_an_explicit_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path / "ignored"))
    explicit = tmp_path / "explicit_models.json"

    store = LLMModelStore(path=explicit)
    store.upsert(
        {
            "display_name": "GPT",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "api_key": FAKE_KEY,
        }
    )

    assert store.path == explicit
    assert explicit.exists()


def test_cloud_credentials_store_resolves_the_data_directory_per_access(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(first))
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
            }
        ]
    )
    assert (first / "cloud_credentials.json").exists()

    monkeypatch.setenv("ADO2GH_DATA_DIR", str(second))

    assert store.path.parent == second
    assert store.load()["sources"] == [], "a source scanned into the old directory must not leak into the new one"

    store.apply_scan(
        [
            {
                "provider": "gcp",
                "completeness": "complete",
                "primary_method": "env_keys",
                "alternate_methods": [],
                "region": "us-central1",
                "project": None,
                "endpoint": None,
                "missing_fields": [],
            }
        ]
    )
    assert (second / "cloud_credentials.json").exists(), "a scan after the switch must write into the new directory"
