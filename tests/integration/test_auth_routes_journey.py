"""End-to-end journey through ``services/accelerator_api/auth_routes.py`` (COV-DRIFT-001, COV-DRIFT-009).

One test per stage of the account lifecycle the console drives: bootstrap the
first administrator, self-register an operator, have the administrator approve
them, sign in, read the session, get refused at an administrator-only endpoint,
be disabled, and be locked out. The existing ``tests/auth`` modules assert the
individual endpoints; this asserts that the stages compose — a session minted at
one stage is still the identity the next stage is judged by, and revoking it
takes effect immediately.

COV-DRIFT-009 records that this feature added 47 test files and none under
``tests/integration/``; this is one of the four journey tests that closes it.

Every password here is an obviously fake literal (CA-003).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import SESSION_COOKIE, AuthService
from ado2gh.state.factory import create_state_db

ADMIN_PASSWORD = "twelve-char-pass"
OPERATOR_PASSWORD = "operator-fake-pass"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Accelerator client on an empty per-test auth database."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "auth_journey.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


def _user_id(client: TestClient, username: str) -> str:
    users = client.get("/v1/auth/users").json()["users"]
    return next(u["id"] for u in users if u["username"] == username)


def test_bootstrap_register_approve_login_deny_disable_journey(client):
    """The whole lifecycle in one pass, asserting the state after every stage."""
    # Stage 1 — an empty platform reports that it needs bootstrapping.
    status = client.get("/v1/auth/bootstrap-status")
    assert status.status_code == 200
    assert status.json()["needs_bootstrap"] is True

    # Stage 2 — the first account is created and signed in by the same call.
    created = client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    assert created.status_code == 201, created.text
    assert client.get("/v1/auth/bootstrap-status").json()["needs_bootstrap"] is False
    assert client.get("/v1/auth/session").json()["authenticated"] is True

    # Stage 3 — an operator self-registers and is parked pending approval.
    registered = client.post(
        "/v1/auth/register",
        json={"username": "operator", "password": OPERATOR_PASSWORD, "display_name": "Op"},
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["pending_approval"] is True
    assert registered.json()["user"]["role"] == "operator"
    assert SESSION_COOKIE not in registered.cookies, (
        "self-registration minted a session before the account was approved"
    )

    # Stage 4 — the pending account cannot sign in yet.
    blocked = client.post(
        "/v1/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "account_pending_approval"

    # Stage 5 — the administrator approves it.
    operator_id = _user_id(client, "operator")
    approved = client.post(f"/v1/auth/users/{operator_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["user"]["status"] == "active"

    # Stage 6 — the administrator signs out; the session stops working at once.
    assert client.post("/v1/auth/logout").status_code == 200
    assert client.get("/v1/auth/session").status_code == 401
    assert client.get("/v1/auth/users").status_code in (401, 403)

    # Stage 7 — the approved operator signs in and is a recognised identity.
    signed_in = client.post(
        "/v1/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD},
    )
    assert signed_in.status_code == 200, signed_in.text
    session = client.get("/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["username"] == "operator"
    assert session.json()["user"]["role"] == "operator"

    # Stage 8 — the operator is refused at every administrator-only endpoint.
    assert client.get("/v1/auth/users").status_code == 403
    assert client.post(
        "/v1/auth/users",
        json={
            "username": "escalated",
            "password": ADMIN_PASSWORD,
            "role": "operator",
            "display_name": "Escalated",
        },
    ).status_code == 403
    assert client.post(f"/v1/auth/users/{operator_id}/disable").status_code == 403

    # Stage 9 — the administrator returns and disables the operator.
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    disabled = client.post(f"/v1/auth/users/{operator_id}/disable")
    assert disabled.status_code == 200

    # Stage 10 — the disabled account can no longer sign in.
    client.post("/v1/auth/logout")
    locked_out = client.post(
        "/v1/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD},
    )
    assert locked_out.status_code in (401, 403)


def test_second_bootstrap_is_refused(client):
    """Bootstrapping is a once-only act; a second attempt cannot mint an admin."""
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    again = client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin2", "password": ADMIN_PASSWORD, "display_name": "Two"},
    )
    assert again.status_code in (400, 403, 409)
    usernames = {u["username"] for u in client.get("/v1/auth/users").json()["users"]}
    assert "admin2" not in usernames


def test_admin_cannot_disable_their_own_account(client):
    """The platform can never be left with no way in."""
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    admin_id = _user_id(client, "admin")
    resp = client.patch(f"/v1/auth/users/{admin_id}", json={"status": "disabled"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot disable your own account"
    assert client.get("/v1/auth/session").json()["authenticated"] is True


def test_admin_created_account_is_active_without_a_separate_approval(client):
    """The admin-create path skips the approval stage the self-service path requires."""
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    created = client.post(
        "/v1/auth/users",
        json={
            "username": "coordinator",
            "password": OPERATOR_PASSWORD,
            "role": "operator",
            "display_name": "Coord",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["user"]["username"] == "coordinator"
    assert OPERATOR_PASSWORD not in created.text, "the password was echoed back"

    client.post("/v1/auth/logout")
    signed_in = client.post(
        "/v1/auth/login", json={"username": "coordinator", "password": OPERATOR_PASSWORD},
    )
    assert signed_in.status_code == 200


def test_duplicate_username_is_refused(client):
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    body = {
        "username": "taken",
        "password": OPERATOR_PASSWORD,
        "role": "operator",
        "display_name": "First",
    }
    assert client.post("/v1/auth/users", json=body).status_code == 201
    again = client.post("/v1/auth/users", json=body)
    assert again.status_code == 400


def test_updating_an_unknown_account_is_404(client):
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    resp = client.patch("/v1/auth/users/no-such-user", json={"display_name": "x"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "User not found"


def test_role_change_takes_effect_on_the_next_session(client):
    """A role change is applied to the account and judged on the next sign-in.

    ``admin`` is deliberately not in the accepted role set, so an existing
    account can never be promoted to administrator through this endpoint — the
    only administrator is the bootstrapped one.
    """
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    client.post(
        "/v1/auth/users",
        json={
            "username": "promoted",
            "password": OPERATOR_PASSWORD,
            "role": "operator",
            "display_name": "P",
        },
    )
    promoted_id = _user_id(client, "promoted")

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "promoted", "password": OPERATOR_PASSWORD})
    assert client.get("/v1/auth/users").status_code == 403

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    refused = client.patch(f"/v1/auth/users/{promoted_id}", json={"role": "admin"})
    assert refused.status_code == 422, "the endpoint accepted a promotion to admin"

    changed = client.patch(f"/v1/auth/users/{promoted_id}", json={"role": "approver"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"]["role"] == "approver"

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"username": "promoted", "password": OPERATOR_PASSWORD})
    assert client.get("/v1/auth/session").json()["user"]["role"] == "approver"
    assert client.get("/v1/auth/users").status_code == 403, (
        "a non-admin role reached the admin-only account listing"
    )


def test_wrong_password_never_mints_a_session(client):
    client.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": ADMIN_PASSWORD, "display_name": "Admin"},
    )
    client.post("/v1/auth/logout")
    resp = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "wrong-password-here"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"
    assert SESSION_COOKIE not in resp.cookies
    assert client.get("/v1/auth/session").status_code == 401
