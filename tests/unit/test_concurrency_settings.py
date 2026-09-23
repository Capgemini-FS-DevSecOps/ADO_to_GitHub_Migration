"""ConcurrencyConfig's defaults and ConcurrencyManager.from_dict's fallback
values must both come from ConcurrencySettings, the one source, rather than
their own separately-hardcoded numbers.
"""
from ado2gh.api.settings_models import AdvancedSettings, ConcurrencySettings
from ado2gh.core.concurrency import ConcurrencyConfig, ConcurrencyManager


def test_concurrency_config_defaults_match_concurrency_settings():
    settings = ConcurrencySettings()
    cfg = ConcurrencyConfig()
    assert cfg.max_git_workers == settings.max_git_workers
    assert cfg.max_repo_workers == settings.max_repo_workers
    assert cfg.max_pipeline_workers == settings.max_pipeline_workers
    assert cfg.max_ado_rps == settings.max_ado_rps
    assert cfg.max_gh_rps == settings.max_gh_rps


def test_advanced_settings_carries_a_concurrency_settings_instance():
    adv = AdvancedSettings()
    assert isinstance(adv.concurrency, ConcurrencySettings)
    assert adv.concurrency.max_repo_workers == 8


def test_from_dict_fallback_uses_the_same_defaults_when_key_missing():
    manager = ConcurrencyManager.from_dict({})
    settings = ConcurrencySettings()
    assert manager.config.max_git_workers == settings.max_git_workers
    assert manager.config.max_repo_workers == settings.max_repo_workers
    assert manager.config.max_pipeline_workers == settings.max_pipeline_workers
    assert manager.config.max_ado_rps == settings.max_ado_rps
    assert manager.config.max_gh_rps == settings.max_gh_rps


def test_from_dict_legacy_aliases_still_work():
    manager = ConcurrencyManager.from_dict({"repo_parallel": 3, "pipeline_parallel": 5})
    assert manager.config.max_repo_workers == 3
    assert manager.config.max_pipeline_workers == 5


def test_from_dict_explicit_values_override_defaults():
    manager = ConcurrencyManager.from_dict({
        "max_git_workers": 1,
        "max_repo_workers": 2,
        "max_pipeline_workers": 3,
        "max_ado_rps": 4.0,
        "max_gh_rps": 5.0,
    })
    assert manager.config.max_git_workers == 1
    assert manager.config.max_repo_workers == 2
    assert manager.config.max_pipeline_workers == 3
    assert manager.config.max_ado_rps == 4.0
    assert manager.config.max_gh_rps == 5.0
