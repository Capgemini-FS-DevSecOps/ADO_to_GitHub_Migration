"""Regression check for register entry GAP-075 (GAP-ACC-05) — feature-route approvals were granted across every profile.

`migrate_guard._live_scope_id` built its scope from the request path and the target
the body names, and nothing else::

    ":".join([path, *(... for f in _SCOPE_FIELDS ...)])

The dashboard path does embed the profile — `migrate_scope_id(profile_id, ...)` —
so the two halves of the same queue disagreed about what an approval identifies.
An approver releases `/v1/migrate/git-mirror` for `Contoso/payments` while profile
A is active; the operator switches the active profile to B and replays the same
request, and `has_approved` finds the standing grant and admits it. Profile B's
credentials, organisation and target then take a live, irreversible migration on a
confirmation given for profile A.

Approvals are never consumed or expired, so the grant stays usable for as long as
the row exists.

Every identifier here is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db
from services.accelerator_api.routes import migrate_guard

_PATH = "/v1/migrate/git-mirror"
_BODY = {"project": "Contoso", "repo_name": "payments"}
_LIVE_BODY = {
    "project": "Contoso", "repo_name": "payments",
    "github_org": "fake-org", "github_repo": "payments", "dry_run": False,
}


def test_the_scope_changes_with_the_active_profile():
    """Two profiles migrating the same repository are two different grants."""
    assert migrate_guard._live_scope_id(_PATH, _BODY, "prod") != (
        migrate_guard._live_scope_id(_PATH, _BODY, "staging")
    ), "a feature-route approval under one profile still names every other profile"


def test_no_active_profile_has_its_own_scope():
    """A deployment with no active profile is not silently every profile."""
    unscoped = migrate_guard._live_scope_id(_PATH, _BODY, None)
    assert unscoped != migrate_guard._live_scope_id(_PATH, _BODY, "prod")
    assert unscoped.startswith(f"{_PATH}:_platform:")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An authenticated accelerator client on a throwaway state database."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _login_operator(client: TestClient) -> None:
    """Bootstrap an admin, add an operator and sign in as them."""
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    client.post(
        "/v1/auth/users",
        json={
            "username": "op075", "password": "twelve-char-pass",
            "role": "operator", "display_name": "op075",
        },
    )
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "op075", "password": "twelve-char-pass"})


def test_a_grant_under_one_profile_does_not_release_another(client, tmp_path, monkeypatch):
    """The guard must park the same live route again after a profile switch."""
    _login_operator(client)
    monkeypatch.setattr(migrate_guard, "active_profile_id", lambda: "prod")

    parked = client.post(_PATH, json=_LIVE_BODY)
    assert parked.status_code == 403
    granted_scope = migrate_guard._live_scope_id(_PATH, _LIVE_BODY, "prod")

    db = create_state_db(str(tmp_path / "approvals.db"))
    db.create_live_execution_approval(
        approval_id="lve_gap075grant",
        requester_user_id="op-1",
        requester_username="op075",
        scope_type="migrate_job",
        scope_id=granted_scope,
        requested_at=datetime.now(timezone.utc).isoformat(),
        profile_id="prod",
        reason_request=f"Live {_PATH}",
        context_json='{"route": "/v1/migrate/git-mirror"}',
    )
    db.decide_live_execution_approval(
        "lve_gap075grant", "approved",
        PlatformUser(id="ap-1", username="approver1",
                     role=PlatformRole.APPROVER, display_name="Approver One"),
        "granted for prod", datetime.now(timezone.utc).isoformat(),
    )

    monkeypatch.setattr(migrate_guard, "active_profile_id", lambda: "staging")
    replayed = client.post(_PATH, json=_LIVE_BODY)

    assert replayed.status_code == 403, (
        f"the live route ran under profile 'staging' on an approval granted for "
        f"'prod' (HTTP {replayed.status_code}); the approver confirmed one profile's "
        f"organisation and credentials, not another's (CA-002)"
    )
    assert replayed.json()["detail"]["code"] == "awaiting_approval"
