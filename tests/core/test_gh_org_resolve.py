"""Tests for GitHub org resolution from profile and migration.yaml."""
from ado2gh.api.profile_discovery import require_gh_org, resolve_gh_org
from ado2gh.api.settings_store import MigrationProfile
import pytest


def test_resolve_gh_org_from_profile():
    profile = MigrationProfile(id="p1", name="test", gh_org="my-org")
    assert resolve_gh_org(profile) == "my-org"


def test_resolve_gh_org_falls_back_to_migration_yaml(tmp_path):
    cfg = tmp_path / "migration.yaml"
    cfg.write_text("global:\n  gh_org: destination-org-test\n", encoding="utf-8")
    profile = MigrationProfile(id="p1", name="test", gh_org="")
    assert resolve_gh_org(profile, config_path=str(cfg)) == "destination-org-test"


def test_require_gh_org_raises_when_missing():
    profile = MigrationProfile(id="p1", name="test", gh_org="")
    with pytest.raises(ValueError, match="gh_org"):
        require_gh_org(profile, global_cfg={})
