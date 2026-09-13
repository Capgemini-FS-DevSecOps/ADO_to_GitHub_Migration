"""GAP-020 (GAP-AUTH-07) — session cookie omits ``Secure``; ``max_age`` hardcoded.

``_set_session_cookie`` (``services/accelerator_api/auth_routes.py``) is the single
place the platform session cookie is issued, and ``_clear_session_cookie`` the single
place it is revoked. The register records two defects in that block:

1. ``secure=`` is never passed, so Starlette defaults it to ``False`` and the sole
   bearer credential for both services rides in cleartext on any deployment that is
   not fronted by an HTTPS-enforcing proxy.
2. ``max_age=8 * 3600`` is a literal rather than the session lifetime the auth service
   actually enforces (``SESSION_HOURS``, ``ado2gh/auth/service.py:16``), so the browser
   copy and the server copy of the session can drift apart.

These tests assert both properties end to end through the accelerator's ``/v1/auth``
routes: the cookie carries ``Secure`` exactly when the deployment is HTTPS (directly,
or via ``X-Forwarded-Proto`` where ``ADO2GH_TRUSTED_PROXY`` declares a proxy — GAP-074)
and never over plain local HTTP, its ``Max-Age`` tracks the expiry the service issued,
and logout clears it with matching attributes.

CA-003: no test here reads or records the cookie *value*; every assertion is on the
attributes of the ``Set-Cookie`` header. Credentials below are obvious fakes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.cookies import Morsel, SimpleCookie

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import SESSION_COOKIE, SESSION_HOURS, AuthService
from ado2gh.state.factory import create_state_db

ADMIN = {"username": "gap020-admin", "password": "not-a-real-pass-12345", "display_name": "Admin"}
LOGIN = {"username": ADMIN["username"], "password": ADMIN["password"]}


@pytest.fixture
def accel(tmp_path, monkeypatch):
    """Accelerator app with an isolated auth store and a bootstrapped admin."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ADO2GH_TRUSTED_PROXY", raising=False)
    db_path = tmp_path / "gap020_auth.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return app


def _client(app, *, https: bool = False) -> TestClient:
    return TestClient(app, base_url="https://testserver" if https else "http://testserver")


def _session_morsel(response) -> Morsel:
    """Parse the session ``Set-Cookie`` header off a response, value never inspected."""
    headers = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{SESSION_COOKIE}=")]
    assert len(headers) == 1, f"expected exactly one session Set-Cookie, got {len(headers)}"
    jar: SimpleCookie = SimpleCookie()
    jar.load(headers[0])
    return jar[SESSION_COOKIE]


def _bootstrap(client: TestClient):
    response = client.post("/v1/auth/bootstrap", json=ADMIN)
    assert response.status_code == 201, response.text
    return response


def test_login_cookie_is_httponly_samesite_and_path_scoped(accel):
    client = _client(accel)
    _bootstrap(client)

    morsel = _session_morsel(client.post("/v1/auth/login", json=LOGIN))

    assert morsel["httponly"], "session cookie must be HttpOnly"
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"


def test_login_cookie_is_secure_over_https(accel):
    client = _client(accel, https=True)
    _bootstrap(client)

    morsel = _session_morsel(client.post("/v1/auth/login", json=LOGIN))

    assert morsel["secure"], "session cookie must carry Secure on an HTTPS deployment"


def test_login_cookie_is_secure_behind_a_tls_terminating_proxy(accel, monkeypatch):
    """GAP-074 narrowed this: the header counts only where a proxy has been declared."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")
    client = _client(accel)
    _bootstrap(client)

    response = client.post("/v1/auth/login", json=LOGIN, headers={"X-Forwarded-Proto": "https"})

    assert _session_morsel(response)["secure"], "Secure must follow X-Forwarded-Proto: https"


def test_login_cookie_is_not_secure_over_plain_http(accel):
    """Local/compose deployments serve the console over HTTP; a Secure cookie would be dropped."""
    client = _client(accel)
    _bootstrap(client)

    morsel = _session_morsel(client.post("/v1/auth/login", json=LOGIN))

    assert not morsel["secure"]
    assert morsel["httponly"]


def test_bootstrap_cookie_carries_the_same_flags(accel):
    client = _client(accel, https=True)

    morsel = _session_morsel(_bootstrap(client))

    assert morsel["secure"]
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"


def test_cookie_max_age_matches_the_expiry_the_service_issued(accel):
    client = _client(accel)
    _bootstrap(client)

    response = client.post("/v1/auth/login", json=LOGIN)
    expires_at = datetime.fromisoformat(response.json()["session_expires_at"])
    enforced = (expires_at - datetime.now(timezone.utc)).total_seconds()

    max_age = int(_session_morsel(response)["max-age"])

    assert abs(max_age - enforced) <= 5, "cookie lifetime must not drift from the server session"
    assert abs(max_age - SESSION_HOURS * 3600) <= 5


def test_cookie_max_age_follows_configured_session_hours(accel, monkeypatch):
    """A hardcoded ``8 * 3600`` cannot satisfy this: the TTL must have one source."""
    from ado2gh.auth import service

    monkeypatch.setattr(service, "SESSION_HOURS", 2)
    client = _client(accel)
    _bootstrap(client)

    max_age = int(_session_morsel(client.post("/v1/auth/login", json=LOGIN))["max-age"])

    assert abs(max_age - 2 * 3600) <= 5


def test_logout_clears_the_cookie_with_matching_attributes(accel):
    client = _client(accel, https=True)
    _bootstrap(client)
    client.post("/v1/auth/login", json=LOGIN)

    morsel = _session_morsel(client.post("/v1/auth/logout"))

    assert int(morsel["max-age"]) == 0
    assert morsel["path"] == "/"
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert morsel["secure"], "the clearing cookie must match the one it replaces"
