"""Profile governance helper tests."""
import os
from dataclasses import dataclass

import pytest

from ado2gh.api.profile_governance import (
    assert_can_delete,
    count_active_profiles,
    needs_profile_setup,
    ProfileGovernanceError,
    ProfileStatus,
)
from ado2gh.api.settings_store import MigrationProfile, SettingsStore


@dataclass
class _StubProfile:
    id: str
    status: str = ProfileStatus.ACTIVE.value
    is_default: bool = False


def test_count_active_and_needs_setup():
    profiles = [
        _StubProfile("a", ProfileStatus.ACTIVE.value),
        _StubProfile("b", ProfileStatus.PENDING_APPROVAL.value),
    ]
    assert count_active_profiles(profiles) == 1
    assert not needs_profile_setup(profiles)
    assert needs_profile_setup([_StubProfile("x", ProfileStatus.DENIED.value)])


def test_last_active_delete_blocked():
    profiles = [_StubProfile("only", ProfileStatus.ACTIVE.value, True)]
    with pytest.raises(ProfileGovernanceError) as exc:
        assert_can_delete(profiles, "only")
    assert exc.value.code == "last_active_profile"


def test_default_delete_requires_replacement():
    profiles = [
        _StubProfile("d", ProfileStatus.ACTIVE.value, True),
        _StubProfile("other", ProfileStatus.ACTIVE.value, False),
    ]
    with pytest.raises(ProfileGovernanceError) as exc:
        assert_can_delete(profiles, "d")
    assert exc.value.code == "default_replacement_required"
    assert_can_delete(profiles, "d", "other")


def test_settings_store_delete_invariant(tmp_path):
    path = tmp_path / "ui_settings.json"
    store = SettingsStore(path)
    p = store.setup_profile(
        {
            "name": "One",
            "ado_org_url": "https://dev.azure.com/x",
            "ado_pat": "pat",
            "gh_org": "org",
            "github_token": "ghp_test",
        },
        role="admin",
    )
    with pytest.raises(ValueError, match="last_active_profile"):
        store.delete_profile(p.id)


def test_settings_store_delete_default_with_replacement(tmp_path):
    path = tmp_path / "ui_settings.json"
    store = SettingsStore(path)
    p1 = store.setup_profile(
        {
            "name": "First",
            "ado_org_url": "https://dev.azure.com/x",
            "ado_pat": "pat",
            "gh_org": "org",
            "github_token": "ghp_test",
        },
        role="admin",
    )
    p2 = store.setup_profile(
        {
            "name": "Second",
            "ado_org_url": "https://dev.azure.com/y",
            "ado_pat": "pat2",
            "gh_org": "org2",
            "github_token": "ghp_test2",
        },
        role="admin",
    )
    store.set_default_profile(p1.id)
    store.delete_profile(p1.id, p2.id)
    remaining = store.load().migration_profiles
    assert len(remaining) == 1
    assert remaining[0].is_default


def test_operator_setup_pending_and_appeal_flow(tmp_path):
    path = tmp_path / "ui_settings.json"
    store = SettingsStore(path)
    store.setup_profile(
        {
            "name": "Admin",
            "ado_org_url": "https://dev.azure.com/x",
            "ado_pat": "pat",
            "gh_org": "org",
            "github_token": "ghp_test",
        },
        role="admin",
    )
    pending = store.setup_profile(
        {
            "name": "Op",
            "ado_org_url": "https://dev.azure.com/y",
            "ado_pat": "pat2",
            "gh_org": "org2",
            "github_token": "ghp_test2",
        },
        role="operator",
        submitted_by="operator",
    )
    assert pending.status == "pending_approval"
    denied = store.deny_profile(pending.id, "no")
    assert denied.status == "denied"
    appealed = store.appeal_profile(pending.id, "operator")
    assert appealed.status == "pending_approval"
    approved = store.approve_profile(pending.id)
    assert approved.status == "active"


def test_deactivate_profile(tmp_path):
    path = tmp_path / "ui_settings.json"
    store = SettingsStore(path)
    p1 = store.setup_profile(
        {
            "name": "First",
            "ado_org_url": "https://dev.azure.com/x",
            "ado_pat": "pat",
            "gh_org": "org",
            "github_token": "ghp_test",
        },
        role="admin",
    )
    p2 = store.setup_profile(
        {
            "name": "Second",
            "ado_org_url": "https://dev.azure.com/y",
            "ado_pat": "pat2",
            "gh_org": "org2",
            "github_token": "ghp_test2",
        },
        role="admin",
    )
    store.set_default_profile(p1.id)
    inactive = store.deactivate_profile(p1.id, p2.id)
    assert inactive.status == "inactive"
    assert store.get_default_profile().id == p2.id


def test_assert_profile_active_for_run():
    from ado2gh.api.profile_governance import assert_profile_active_for_run

    with pytest.raises(ProfileGovernanceError):
        assert_profile_active_for_run(_StubProfile("x", ProfileStatus.DENIED.value))


def test_onboarding_status_payload_operator():
    from ado2gh.api.profile_governance import onboarding_status_payload
    from ado2gh.auth.models import PlatformRole

    payload = onboarding_status_payload([], PlatformRole.OPERATOR)
    assert payload["can_submit_profile"] is False
    assert payload["blocked_message"]


def test_operator_submit_blocked_without_active(tmp_path):
    path = tmp_path / "ui_settings.json"
    store = SettingsStore(path)
    with pytest.raises(ValueError, match="operator_submit_blocked"):
        store.setup_profile(
            {
                "name": "Op",
                "ado_org_url": "https://dev.azure.com/y",
                "ado_pat": "pat2",
                "gh_org": "org2",
                "github_token": "ghp_test2",
            },
            role="operator",
            submitted_by="operator",
        )
