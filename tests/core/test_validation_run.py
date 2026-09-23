"""Profile- and upload-backed validation."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ado2gh.api.contracts import ValidateRequest
from ado2gh.api.settings_store import AdvancedSettings, MigrationProfile, SettingsStore, UISettings
from ado2gh.api.validation_run import (
    build_global_cfg,
    resolve_validation_repos,
    run_validation,
)
from ado2gh.models import DEFAULT_MIGRATION_STRATEGY, RepoConfig


def test_advanced_settings_default_migration_strategy_is_gei():
    assert AdvancedSettings().migration_strategy == DEFAULT_MIGRATION_STRATEGY
    assert DEFAULT_MIGRATION_STRATEGY == "gei"


def test_build_global_cfg_from_profile_without_file():
    profile = MigrationProfile(
        id="p1",
        name="Test",
        ado_org_url="https://dev.azure.com/org",
        ado_pat="pat",
        gh_org="gh-org",
        status="active",
    )
    adv = AdvancedSettings(migration_strategy="mirror", repo_parallel=4, pipeline_parallel=8)
    cfg, waves = build_global_cfg(profile, adv)
    assert cfg["ado_org_url"] == "https://dev.azure.com/org"
    assert cfg["gh_org"] == "gh-org"
    assert waves == []


def test_build_global_cfg_from_uploaded_yaml():
    profile = MigrationProfile(id="p1", name="Test", gh_org="from-profile", status="active")
    adv = AdvancedSettings()
    yaml_text = """
global:
  gh_org: from-yaml
  migration_strategy: gei
waves: []
"""
    cfg, _ = build_global_cfg(profile, adv, config_yaml=yaml_text)
    assert cfg["gh_org"] == "from-profile"
    assert cfg["migration_strategy"] == "gei"


def test_resolve_repos_from_input_text():
    adv = AdvancedSettings(default_phase="poc")
    global_cfg = {"gh_org": "my-org", "default_scopes": ["repo"]}
    repos = resolve_validation_repos(
        ValidateRequest(input_text="ProjA/repo1\n# comment\nProjB/repo2"),
        profile=None,
        advanced=adv,
        global_cfg=global_cfg,
        waves=[],
    )
    assert len(repos) == 2
    assert repos[0].ado_project == "ProjA"


def test_run_validation_uses_profile_phase_repos(tmp_path):
    store = SettingsStore(path=tmp_path / "ui_settings.json")
    profile = MigrationProfile(
        id="p1",
        name="Test",
        ado_org_url="https://dev.azure.com/org",
        ado_pat="pat",
        gh_org="gh-org",
        status="active",
    )
    store.save(UISettings(active_profile_id="p1", migration_profiles=[profile]))

    sample_repos = [
        RepoConfig(
            ado_project="P",
            ado_repo="r1",
            gh_org="gh-org",
            gh_repo="r1",
            scopes=["repo"],
        ),
    ]
    validate_result = {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "output_path": "output/validation_report.csv",
        "details": [{"overall": "PASS", "project": "P", "repo": "r1"}],
    }

    with patch(
        "ado2gh.api.validation_run.repo_configs_for_phase",
        return_value=sample_repos,
    ), patch(
        "ado2gh.api.validation_run._build_ado_client",
        return_value=MagicMock(),
    ), patch(
        "ado2gh.api.validation_run._build_gh_client",
        return_value=MagicMock(),
    ), patch(
        "ado2gh.api.validation_run.create_state_db",
        return_value=MagicMock(),
    ), patch(
        "ado2gh.api.validation_run.PostMigrationValidator",
    ) as mock_validator_cls:
        mock_validator_cls.return_value.validate.return_value = validate_result["details"]
        result = run_validation(
            ValidateRequest(profile_id="p1", phase="poc"),
            store,
        )

    assert result.total == 1
    assert result.passed == 1
