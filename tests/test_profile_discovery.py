"""Tests for profile discovery → risk score sync."""
from ado2gh.api.profile_discovery import (
    iter_scan_repos,
    sync_profile_scan_to_risk_scores,
)
from ado2gh.state.db import StateDB


def test_sync_profile_scan_to_risk_scores(tmp_path):
    db = StateDB(tmp_path / "state.db")
    profile_id = "prof-sync"
    scan = {
        "scanned_at": "2026-06-08T12:00:00+00:00",
        "gh_org": "acme",
        "repos_scanned": 2,
        "recommendations": {
            "poc": {
                "phase": "poc",
                "repos": [
                    {"project": "P1", "repo_name": "alpha", "total_score": 12, "assigned_phase": "poc"},
                    {"project": "P1", "repo_name": "beta", "total_score": 20, "assigned_phase": "poc"},
                ],
            },
        },
    }
    db.save_profile_scan(profile_id, scan)
    count = sync_profile_scan_to_risk_scores(profile_id, scan, db_path=str(tmp_path / "state.db"))
    assert count == 2
    poc = db.get_risk_scores_for_phase("poc")
    assert len(poc) == 2


def test_sync_prunes_stale_risk_scores(tmp_path):
    from ado2gh.api.profile_discovery import risk_score_from_repo_dict

    db = StateDB(tmp_path / "state.db")
    db.upsert_risk_score(risk_score_from_repo_dict({
        "project": "P1", "repo_name": "beta", "total_score": 20, "assigned_phase": "poc",
    }))
    scan = {
        "gh_org": "acme",
        "recommendations": {
            "poc": {
                "phase": "poc",
                "repos": [
                    {"project": "RealProj", "repo_name": "real-repo", "total_score": 12, "assigned_phase": "poc"},
                ],
            },
        },
    }
    sync_profile_scan_to_risk_scores("prof", scan, db_path=str(tmp_path / "state.db"))
    poc = db.get_risk_scores_for_phase("poc")
    assert len(poc) == 1
    assert poc[0]["project"] == "RealProj"
    assert poc[0]["repo_name"] == "real-repo"


def test_iter_scan_repos_assigns_phase_from_bucket():
    scan = {
        "recommendations": {
            "pilot": {
                "phase": "pilot",
                "repos": [{"project": "P", "repo_name": "r1", "total_score": 40}],
            },
        },
    }
    repos = iter_scan_repos(scan)
    assert repos[0]["assigned_phase"] == "pilot"
