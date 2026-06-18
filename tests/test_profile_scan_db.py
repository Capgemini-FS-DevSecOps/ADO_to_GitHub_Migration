"""Tests for profile scan persistence in StateDB."""
from pathlib import Path

import pytest

from ado2gh.state.db import StateDB


@pytest.fixture
def db(tmp_path: Path) -> StateDB:
    return StateDB(tmp_path / "test_state.db")


def _sample_scan() -> dict:
    return {
        "scanned_at": "2026-06-08T12:00:00+00:00",
        "gh_org": "acme-github",
        "projects_scanned": 2,
        "repos_scanned": 3,
        "recommendations": {
            "poc": {
                "phase": "poc",
                "repo_count": 2,
                "risk_min": 10,
                "risk_max": 25,
                "rationale": "Low risk",
                "repos": [
                    {
                        "project": "P1",
                        "repo_name": "alpha",
                        "total_score": 10,
                        "assigned_phase": "poc",
                        "pipeline_count": 1,
                    },
                    {
                        "project": "P1",
                        "repo_name": "beta",
                        "total_score": 25,
                        "assigned_phase": "poc",
                        "pipeline_count": 0,
                    },
                ],
            },
            "pilot": {
                "phase": "pilot",
                "repo_count": 1,
                "risk_min": 45,
                "risk_max": 45,
                "rationale": "Medium risk",
                "repos": [
                    {
                        "project": "P2",
                        "repo_name": "gamma",
                        "total_score": 45,
                        "assigned_phase": "pilot",
                        "pipeline_count": 3,
                    },
                ],
            },
        },
    }


def test_save_and_load_profile_scan(db: StateDB):
    profile_id = "prof-1"
    raw = _sample_scan()
    db.save_profile_scan(profile_id, raw)

    payload = db.build_profile_scan_payload(profile_id)
    assert payload is not None
    assert payload["repos_scanned"] == 3
    assert payload["gh_org"] == "acme-github"
    assert sum(b["repo_count"] for b in payload["recommendations"].values()) == 3

    repos = db.get_profile_scan_repos(profile_id)
    assert len(repos) == 3


def test_phase_assignment_override(db: StateDB):
    profile_id = "prof-2"
    db.save_profile_scan(profile_id, _sample_scan())

    updated = db.update_profile_repo_phases(profile_id, [
        {"project": "P2", "repo_name": "gamma", "assigned_phase": "wave1"},
        {"project": "P1", "repo_name": "alpha", "assigned_phase": "pilot"},
    ])
    assert updated == 2

    payload = db.build_profile_scan_payload(profile_id)
    assert "wave1" in payload["recommendations"]
    assert payload["recommendations"]["wave1"]["repo_count"] == 1
    gamma = next(
        r for r in db.get_profile_scan_repos(profile_id) if r["repo_name"] == "gamma"
    )
    assert gamma["assigned_phase"] == "wave1"
    assert gamma["suggested_phase"] == "pilot"


def test_rescan_preserves_manual_phase_overrides(db: StateDB):
    profile_id = "prof-rescan"
    db.save_profile_scan(profile_id, _sample_scan())
    db.update_profile_repo_phases(profile_id, [
        {"project": "P1", "repo_name": "alpha", "assigned_phase": "wave1"},
    ])

    rescan = _sample_scan()
    rescan["scanned_at"] = "2026-06-09T12:00:00+00:00"
    db.save_profile_scan(profile_id, rescan, preserve_manual_assignments=True)

    alpha = next(
        r for r in db.get_profile_scan_repos(profile_id) if r["repo_name"] == "alpha"
    )
    assert alpha["assigned_phase"] == "wave1"
    assert alpha["suggested_phase"] == "poc"

    payload = db.build_profile_scan_payload(profile_id)
    assert payload is not None
    assert "wave1" in payload["recommendations"]


def test_build_wave_uses_profile_scan_phase_assignments(db: StateDB):
    from ado2gh.api.profile_discovery import build_wave_from_profile_phase
    from ado2gh.api.settings_store import MigrationProfile

    profile_id = "prof-wave"
    db.save_profile_scan(profile_id, _sample_scan())
    db.update_profile_repo_phases(profile_id, [
        {"project": "P1", "repo_name": "beta", "assigned_phase": "pilot"},
    ])

    profile = MigrationProfile(
        id=profile_id,
        name="test",
        ado_org_url="https://dev.azure.com/org",
        ado_pat="***",
        gh_org="acme-github",
        is_default=True,
    )
    wave = build_wave_from_profile_phase("poc", profile, db_path=str(db.db_path))
    assert wave is not None
    repo_names = {r.ado_repo for r in wave.repos}
    assert repo_names == {"alpha"}
    assert "beta" not in repo_names
