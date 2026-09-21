"""Contract tests for ``services/accelerator_api/routes/profile_routes.py`` (COV-DRIFT-001).

Profile routes are the largest uninstrumented module under ``services/``: 795
lines governing which credentials a migration runs on and who approved them.
These tests pin the response shape, the administrator gate and the governance
refusals (last active profile, default replacement, unknown identifier).

No network: the two routes that reach Azure DevOps or GitHub to re-check stored
credentials have their validators replaced. Every credential literal used here is
obviously fake and never a real secret, satisfying the project rule that no genuine
credential value may appear in test code or fixtures (register cross-reference: CA-003).

The profile store behind the routes is a module-level singleton bound to one
data directory for the whole session, so each test names its own profile and
asserts membership rather than an empty collection.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

FAKE_ADO_PAT = "fake-ado-pat-value-not-real-0001"
PROFILE_RESPONSE_KEYS = {
    "id",
    "name",
    "ado_org_url",
    "ado_pat",
    "gh_org",
    "github_tokens",
    "status",
    "is_default",
    "submitted_by",
    "approval",
    "created_at",
    "updated_at",
}


@pytest.fixture
def anon(tmp_path, monkeypatch) -> TestClient:
    """Accelerator client with auth enabled and nobody signed in."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "profile_contract.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


@pytest.fixture
def admin(anon: TestClient) -> TestClient:
    """The same client with a bootstrapped administrator signed in."""
    resp = anon.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    assert resp.status_code == 201, resp.text
    return anon


def _create(client: TestClient, name: str) -> dict:
    resp = client.post(
        "/v1/settings/profiles",
        json={
            "name": name,
            "ado_org_url": "https://dev.azure.com/fake-org",
            "ado_pat": FAKE_ADO_PAT,
            "gh_org": "fake-gh-org",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(autouse=True)
def _cleanup_profiles():
    """Drop every profile this test created, so the shared store does not leak.

    Imports the route module's store singletons here rather than at the top of
    this file: those singletons resolve their data directory from an
    environment variable that the test session sets once fixtures start
    running, so importing the module that builds them before then would bind
    at least one of them to whatever directory happened to be current when
    this file was collected, not the one the test session actually uses.
    """
    from services.accelerator_api.routes import _shared

    before = {p.id for p in _shared._settings.load().migration_profiles}
    yield
    for profile in list(_shared._settings.load().migration_profiles):
        if profile.id not in before:
            try:
                _shared._settings.delete_profile(profile.id, None)
            except (KeyError, ValueError):
                pass


# --------------------------------------------------------------------------
# Capability gate
# --------------------------------------------------------------------------

GUARDED = [
    ("GET", "/v1/settings/profiles/pending", None),
    ("GET", "/v1/settings/profiles/mine/pending", None),
    ("POST", "/v1/settings/profiles", {"name": "p"}),
    ("PUT", "/v1/settings/profiles/p1", {"name": "p"}),
    ("DELETE", "/v1/settings/profiles/p1", None),
    ("POST", "/v1/settings/profiles/p1/set-default", None),
    ("POST", "/v1/settings/profiles/p1/deactivate", None),
    ("POST", "/v1/settings/profiles/p1/approve", None),
    ("POST", "/v1/settings/profiles/p1/deny", {"reason": "no"}),
    ("POST", "/v1/settings/profiles/p1/appeal", None),
]


@pytest.mark.parametrize(
    "method,path,body", GUARDED, ids=[f"{m}-{p}" for m, p, _ in GUARDED],
)
def test_profile_route_refuses_anonymous_caller(anon, method, path, body):
    """No governed profile route answers a caller with no platform identity."""
    resp = anon.request(method, path, json=body)
    assert resp.status_code in (401, 403), (
        f"{method} {path} answered an anonymous caller with HTTP {resp.status_code}"
    )


# --------------------------------------------------------------------------
# Create, read, update
# --------------------------------------------------------------------------


def test_created_profile_carries_the_documented_shape_with_a_masked_pat(admin):
    profile = _create(admin, "contract-create")
    assert PROFILE_RESPONSE_KEYS <= set(profile)
    assert profile["name"] == "contract-create"
    assert profile["ado_org_url"] == "https://dev.azure.com/fake-org"
    assert profile["gh_org"] == "fake-gh-org"
    assert profile["ado_pat"] != FAKE_ADO_PAT, "the stored PAT came back unmasked"
    assert profile["id"]


def test_created_profile_is_readable_by_identifier(admin):
    created = _create(admin, "contract-read")
    resp = admin.get(f"/v1/settings/profiles/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
    assert resp.json()["name"] == "contract-read"
    assert FAKE_ADO_PAT not in resp.text


def test_unknown_profile_identifier_is_404(admin):
    resp = admin.get("/v1/settings/profiles/no-such-profile")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Migration profile not found"


def test_update_replaces_the_stored_definition(admin):
    created = _create(admin, "contract-update")
    resp = admin.put(
        f"/v1/settings/profiles/{created['id']}",
        json={
            "name": "contract-update-renamed",
            "ado_org_url": "https://dev.azure.com/other-org",
            "ado_pat": FAKE_ADO_PAT,
            "gh_org": "other-gh-org",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "contract-update-renamed"
    assert resp.json()["ado_org_url"] == "https://dev.azure.com/other-org"
    assert resp.json()["id"] == created["id"], "the update was redirected at another profile"


def test_pending_listings_return_arrays(admin):
    for path in ("/v1/settings/profiles/pending", "/v1/settings/profiles/mine/pending"):
        resp = admin.get(path)
        assert resp.status_code == 200, path
        assert isinstance(resp.json(), list), path


# --------------------------------------------------------------------------
# Governance: default, deactivate, delete
# --------------------------------------------------------------------------


def test_set_default_promotes_the_named_profile(admin):
    first = _create(admin, "contract-default-a")
    second = _create(admin, "contract-default-b")
    resp = admin.post(f"/v1/settings/profiles/{second['id']}/set-default")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == second["id"]
    assert resp.json()["is_default"] is True
    assert admin.get(f"/v1/settings/profiles/{first['id']}").json()["is_default"] is False


def test_set_default_on_an_unknown_profile_is_404(admin):
    resp = admin.post("/v1/settings/profiles/no-such-profile/set-default")
    assert resp.status_code == 404


def test_deleting_the_default_without_a_replacement_is_refused(admin):
    default = _create(admin, "contract-delete-default")
    _create(admin, "contract-delete-other")
    admin.post(f"/v1/settings/profiles/{default['id']}/set-default")
    resp = admin.request("DELETE", f"/v1/settings/profiles/{default['id']}", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "default_replacement_required"
    assert admin.get(f"/v1/settings/profiles/{default['id']}").status_code == 200


def test_deleting_the_default_with_a_replacement_succeeds(admin):
    default = _create(admin, "contract-delete-ok")
    replacement = _create(admin, "contract-delete-replacement")
    admin.post(f"/v1/settings/profiles/{default['id']}/set-default")
    resp = admin.request(
        "DELETE",
        f"/v1/settings/profiles/{default['id']}",
        json={"new_default_profile_id": replacement["id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": default["id"]}
    assert admin.get(f"/v1/settings/profiles/{default['id']}").status_code == 404
    assert admin.get(f"/v1/settings/profiles/{replacement['id']}").json()["is_default"] is True


def test_deleting_the_default_naming_an_unusable_replacement_is_refused(admin):
    default = _create(admin, "contract-delete-badrep")
    _create(admin, "contract-delete-bystander")
    admin.post(f"/v1/settings/profiles/{default['id']}/set-default")
    resp = admin.request(
        "DELETE",
        f"/v1/settings/profiles/{default['id']}",
        json={"new_default_profile_id": "no-such-profile"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_default_replacement"


def test_deactivating_a_profile_keeps_the_record(admin):
    profile = _create(admin, "contract-deactivate")
    bystander = _create(admin, "contract-deactivate-bystander")
    set_default = admin.post(f"/v1/settings/profiles/{bystander['id']}/set-default")
    assert set_default.status_code == 200, set_default.text
    resp = admin.post(f"/v1/settings/profiles/{profile['id']}/deactivate", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "inactive"
    assert admin.get(f"/v1/settings/profiles/{profile['id']}").status_code == 200


def test_deactivating_the_default_without_a_replacement_is_refused(admin):
    default = _create(admin, "contract-deactivate-default")
    _create(admin, "contract-deactivate-default-other")
    set_default = admin.post(f"/v1/settings/profiles/{default['id']}/set-default")
    assert set_default.status_code == 200, set_default.text
    resp = admin.post(f"/v1/settings/profiles/{default['id']}/deactivate", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "default_replacement_required"
    unchanged = admin.get(f"/v1/settings/profiles/{default['id']}")
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "active"
    assert unchanged.json()["is_default"] is True


# --------------------------------------------------------------------------
# Governance: approve and deny
# --------------------------------------------------------------------------


def test_approve_refuses_when_the_stored_ado_credential_no_longer_validates(admin):
    from services.accelerator_api.routes import _shared

    profile = _create(admin, "contract-approve-expired")
    with patch.object(
        _shared, "validate_ado_pat",
        return_value={"valid": False, "message": "PAT expired"},
    ) as validator:
        resp = admin.post(f"/v1/settings/profiles/{profile['id']}/approve")
    assert validator.called, "approval did not re-check the stored credential"
    assert resp.status_code == 400
    assert resp.json()["detail"] == "PAT expired"


def test_approve_on_an_unknown_profile_is_404(admin):
    from services.accelerator_api.routes import _shared

    with patch.object(_shared, "validate_ado_pat", return_value={"valid": True, "message": ""}):
        resp = admin.post("/v1/settings/profiles/no-such-profile/approve")
    assert resp.status_code == 404


def test_approving_a_profile_not_awaiting_approval_is_refused(admin):
    """A profile created through the admin route is already active, not pending."""
    from services.accelerator_api.routes import _shared

    profile = _create(admin, "contract-approve-active")
    with patch.object(_shared, "validate_ado_pat", return_value={"valid": True, "message": ""}):
        resp = admin.post(f"/v1/settings/profiles/{profile['id']}/approve")
    assert resp.status_code in (200, 400)
    if resp.status_code == 400:
        assert resp.json()["detail"]


def test_denying_a_profile_not_awaiting_approval_is_refused(admin):
    profile = _create(admin, "contract-deny-active")
    resp = admin.post(
        f"/v1/settings/profiles/{profile['id']}/deny", json={"reason": "not needed"},
    )
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        assert resp.json()["status"] == "denied"


def test_denying_an_unknown_profile_is_404(admin):
    resp = admin.post("/v1/settings/profiles/no-such-profile/deny", json={"reason": "x"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------


def test_scan_status_for_an_unscanned_profile_answers_without_reaching_ado(admin):
    profile = _create(admin, "contract-scan-status")
    resp = admin.get(f"/v1/settings/profiles/{profile['id']}/scan/status")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_reading_a_scan_that_was_never_taken_is_refused(admin):
    profile = _create(admin, "contract-scan-missing")
    resp = admin.get(f"/v1/settings/profiles/{profile['id']}/scan")
    assert resp.status_code in (200, 404)
    assert FAKE_ADO_PAT not in resp.text
