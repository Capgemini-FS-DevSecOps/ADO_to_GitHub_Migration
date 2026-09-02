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
            "unassigned": {
                "phase": "unassigned",
                "repos": [
                    {"project": "P1", "repo_name": "alpha", "total_score": 12},
                    {"project": "P1", "repo_name": "beta", "total_score": 20},
                ],
            },
        },
    }
    db.save_profile_scan(profile_id, scan)
    count = sync_profile_scan_to_risk_scores(profile_id, scan, db_path=str(tmp_path / "state.db"))
    assert count == 2
    all_scores = db.get_risk_scores_for_phase(None)
    assert len(all_scores) == 2


def test_sync_prunes_stale_risk_scores(tmp_path):
    from ado2gh.api.profile_discovery import risk_score_from_repo_dict

    db = StateDB(tmp_path / "state.db")
    db.upsert_risk_score(risk_score_from_repo_dict({
        "project": "P1", "repo_name": "beta", "total_score": 20,
    }))
    scan = {
        "gh_org": "acme",
        "recommendations": {
            "unassigned": {
                "phase": "unassigned",
                "repos": [
                    {"project": "RealProj", "repo_name": "real-repo", "total_score": 12},
                ],
            },
        },
    }
    sync_profile_scan_to_risk_scores("prof", scan, db_path=str(tmp_path / "state.db"))
    all_scores = db.get_risk_scores_for_phase(None)
    assert len(all_scores) == 1
    assert all_scores[0]["project"] == "RealProj"
    assert all_scores[0]["repo_name"] == "real-repo"


def test_iter_scan_repos_does_not_assign_phase():
    scan = {
        "recommendations": {
            "pilot": {
                "phase": "pilot",
                "repos": [{"project": "P", "repo_name": "r1", "total_score": 40}],
            },
        },
    }
    repos = iter_scan_repos(scan)
    assert "assigned_phase" not in repos[0]
    assert "suggested_phase" not in repos[0]
