"""Background profile scan job lifecycle."""
from __future__ import annotations

import time
from unittest.mock import patch

from ado2gh.api.settings_store import MigrationProfile, SettingsStore, UISettings


def test_start_profile_scan_runs_in_background(tmp_path):
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

    scan_done = {"called": False}

    def fake_execute(self, profile_id, **kwargs):
        scan_done["called"] = True
        time.sleep(0.05)
        return {
            "scanned_at": "2026-06-18T12:00:00+00:00",
            "repos_scanned": 10,
            "projects_scanned": 2,
            "org_inventory": {"total_service_connections": 3},
        }

    with patch.object(SettingsStore, "_execute_profile_scan", fake_execute):
        started = store.start_profile_scan("p1")

    assert started["status"] == "started"
    assert store.profile_rescan_status("p1")["running"] is True

    deadline = time.time() + 2
    while time.time() < deadline:
        status = store.profile_rescan_status("p1")
        if not status["running"]:
            break
        time.sleep(0.02)

    assert scan_done["called"] is True
    final = store.profile_rescan_status("p1")
    assert final["running"] is False
    assert final["status"] == "completed"
    assert final["repos_scanned"] == 10
    assert final["service_connections"] == 3
