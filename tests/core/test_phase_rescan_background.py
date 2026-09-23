"""Background re-scan after phase save."""
from __future__ import annotations

import time
from unittest.mock import patch

from ado2gh.api.settings_store import MigrationProfile, SettingsStore, UISettings

VALID_PHASES = [
    {"id": "poc", "name": "POC", "risk_max": 25, "repo_cap": 10, "order": 0},
    {"id": "wave3", "name": "Wave 3", "risk_max": 100, "repo_cap": 9999, "order": 1},
]

def test_update_phases_starts_background_rescan(tmp_path):
    store = SettingsStore(path=tmp_path / "ui_settings.json")
    profile = MigrationProfile(
        id="p1",
        name="Test",
        ado_org_url="https://dev.azure.com/org",
        ado_pat="pat",
        gh_org="gh-org",
        status="active",
    )
    settings = UISettings(
        active_profile_id="p1",
        migration_profiles=[profile],
    )
    store.save(settings)

    scan_done = {"called": False}

    def fake_rescan(self, profile_id, phases):
        scan_done["called"] = True
        time.sleep(0.05)
        return {"profile_id": profile_id, "repos_scanned": 3}

    with patch.object(SettingsStore, "_rescan_profile_after_phase_change", fake_rescan):
        result = store.update_phases(VALID_PHASES, profile_id="p1")

    assert result["rescan"]["status"] == "started"
    assert result["rescan"]["profile_id"] == "p1"
    assert store.profile_rescan_status("p1")["running"] is True

    deadline = time.time() + 2
    while time.time() < deadline:
        if not store.profile_rescan_status("p1")["running"]:
            break
        time.sleep(0.02)

    assert scan_done["called"] is True
    assert store.profile_rescan_status("p1")["running"] is False


def test_update_phases_skips_rescan_without_credentials(tmp_path):
    store = SettingsStore(path=tmp_path / "ui_settings.json")
    profile = MigrationProfile(id="p1", name="Test", status="active")
    store.save(UISettings(active_profile_id="p1", migration_profiles=[profile]))

    result = store.update_phases(VALID_PHASES, profile_id="p1")

    assert result["rescan"]["skipped"] is True
    assert store.profile_rescan_status("p1")["running"] is False
