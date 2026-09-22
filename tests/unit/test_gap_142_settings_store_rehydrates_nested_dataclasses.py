"""GAP-142: SettingsStore.load() must rebuild AdvancedSettings' nested
dataclass fields (concurrency, agent_runtime) into their declared dataclass
types, not leave them as the plain dicts a JSON round trip produces.
"""
import json

from ado2gh.api.settings_models import AdvancedSettings, AgentRuntimeSettings, ConcurrencySettings
from ado2gh.api.settings_store import SettingsStore


def test_save_then_load_keeps_nested_dataclasses_and_their_changed_values(tmp_path):
    store = SettingsStore(tmp_path / "ui_settings.json")
    settings = store.load()
    settings.advanced.concurrency.max_repo_workers = 42
    settings.advanced.agent_runtime.max_iterations = 7
    store.save(settings)

    reloaded = store.load()
    assert isinstance(reloaded.advanced.concurrency, ConcurrencySettings)
    assert isinstance(reloaded.advanced.agent_runtime, AgentRuntimeSettings)
    assert reloaded.advanced.concurrency.max_repo_workers == 42
    assert reloaded.advanced.agent_runtime.max_iterations == 7


def test_persisted_file_missing_nested_keys_loads_defaults(tmp_path):
    path = tmp_path / "ui_settings.json"
    path.write_text(json.dumps({
        "active_profile_id": None,
        "migration_profiles": [],
        "advanced": {"db_path": "legacy.db"},
    }))
    settings = SettingsStore(path).load()
    assert isinstance(settings.advanced.concurrency, ConcurrencySettings)
    assert isinstance(settings.advanced.agent_runtime, AgentRuntimeSettings)
    assert settings.advanced.concurrency == ConcurrencySettings()
    assert settings.advanced.agent_runtime.max_iterations == AgentRuntimeSettings().max_iterations


def test_persisted_nested_dict_with_unknown_key_loads_without_raising(tmp_path):
    path = tmp_path / "ui_settings.json"
    path.write_text(json.dumps({
        "active_profile_id": None,
        "migration_profiles": [],
        "advanced": {
            "concurrency": {"max_repo_workers": 5, "made_up_field": "ignored"},
        },
    }))
    settings = SettingsStore(path).load()
    assert isinstance(settings.advanced.concurrency, ConcurrencySettings)
    assert settings.advanced.concurrency.max_repo_workers == 5
    assert not hasattr(settings.advanced.concurrency, "made_up_field")


def test_legacy_v1_document_also_rehydrates_advanced(tmp_path):
    path = tmp_path / "ui_settings.json"
    path.write_text(json.dumps({
        "profiles": [],
        "github_tokens": [],
        "advanced": {"agent_runtime": {"max_iterations": 3}},
    }))
    settings = SettingsStore(path).load()
    assert isinstance(settings.advanced.agent_runtime, AgentRuntimeSettings)
    assert settings.advanced.agent_runtime.max_iterations == 3


def test_advanced_settings_still_constructs_with_no_overrides():
    assert isinstance(AdvancedSettings().concurrency, ConcurrencySettings)
    assert isinstance(AdvancedSettings().agent_runtime, AgentRuntimeSettings)
