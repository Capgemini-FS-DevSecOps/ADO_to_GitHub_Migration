"""Contract path assertions for profile onboarding APIs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "specs" / "archive" / "005-profile-onboarding" / "contracts"


def test_onboarding_contract_paths_exist():
    assert (CONTRACT_DIR / "profile-onboarding-api.md").is_file()
    assert (CONTRACT_DIR / "profile-approval-api.md").is_file()


def test_onboarding_api_routes_documented():
    text = (CONTRACT_DIR / "profile-onboarding-api.md").read_text(encoding="utf-8")
    for route in [
        "GET /v1/onboarding/status",
        "POST /v1/settings/profiles/setup",
        "DELETE /v1/settings/profiles/{profile_id}",
        "POST /v1/settings/profiles/{profile_id}/set-default",
        "POST /v1/settings/profiles/{profile_id}/deactivate",
    ]:
        assert route in text


def test_approval_api_routes_documented():
    text = (CONTRACT_DIR / "profile-approval-api.md").read_text(encoding="utf-8")
    for route in [
        "GET /v1/settings/profiles/pending",
        "POST /v1/settings/profiles/{profile_id}/approve",
        "POST /v1/settings/profiles/{profile_id}/deny",
        "POST /v1/settings/profiles/{profile_id}/appeal",
    ]:
        assert route in text
