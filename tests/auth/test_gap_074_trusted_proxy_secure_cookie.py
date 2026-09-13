"""GAP-074 — ``X-Forwarded-Proto`` may only override the scheme behind a declared proxy.

``_is_https_deployment`` (``services/accelerator_api/auth_routes.py``) decides whether the
session cookie carries ``Secure``. It used to believe ``X-Forwarded-Proto`` unconditionally
and read the *first* element of the list, so any client could send ``X-Forwarded-Proto:
http`` on a genuine HTTPS request and have its own session cookie issued without ``Secure``
— downgrading the sole bearer credential for both services to one a network attacker can
read off a later plain-HTTP request.

The fix makes the transport the request actually arrived on the default answer, and gates
every forwarding header behind ``ADO2GH_TRUSTED_PROXY`` (FR-024 contract change — new
environment variable, approved by operator instruction, 2026-09-13):

* unset (the shipped default) — headers are ignored, in both directions;
* set — the **last** element wins, because each hop appends its own value and the last one
  is the hop the operator declared trusted. RFC 7239 ``Forwarded`` is read the same way
  when ``X-Forwarded-Proto`` is absent.

CA-003: no test here reads the cookie *value*; every assertion is on ``Set-Cookie``
attributes, and the credentials below are obvious fakes.
"""
from __future__ import annotations

from http.cookies import Morsel, SimpleCookie
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import SESSION_COOKIE, AuthService
from ado2gh.state.factory import create_state_db

REPO_ROOT = Path(__file__).resolve().parents[2]

ADMIN = {"username": "gap074-admin", "password": "not-a-real-pass-12345", "display_name": "Admin"}
LOGIN = {"username": ADMIN["username"], "password": ADMIN["password"]}


@pytest.fixture
def accel(tmp_path, monkeypatch):
    """Accelerator app with an isolated auth store, a bootstrapped admin and no proxy declared."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ADO2GH_TRUSTED_PROXY", raising=False)
    db_path = tmp_path / "gap074_auth.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    client = TestClient(app, base_url="http://testserver")
    assert client.post("/v1/auth/bootstrap", json=ADMIN).status_code == 201
    return app


def _client(app, *, https: bool) -> TestClient:
    return TestClient(app, base_url="https://testserver" if https else "http://testserver")


def _is_secure(response) -> bool:
    """Report whether the session ``Set-Cookie`` on this response carries ``Secure``."""
    headers = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{SESSION_COOKIE}=")]
    assert len(headers) == 1, f"expected exactly one session Set-Cookie, got {len(headers)}"
    jar: SimpleCookie = SimpleCookie()
    jar.load(headers[0])
    morsel: Morsel = jar[SESSION_COOKIE]
    return bool(morsel["secure"])


def _login(app, *, https: bool, headers: dict[str, str] | None = None):
    response = _client(app, https=https).post("/v1/auth/login", json=LOGIN, headers=headers or {})
    assert response.status_code == 200, response.text
    return response


def test_header_cannot_downgrade_secure_on_a_real_https_request(accel):
    """The reported defect: a client-sent ``http`` stripped ``Secure`` off its own cookie."""
    assert _is_secure(_login(accel, https=True, headers={"X-Forwarded-Proto": "http"}))


def test_header_cannot_upgrade_to_secure_without_a_declared_proxy(accel):
    """A forged ``https`` over plain HTTP would issue a cookie the browser then drops."""
    assert not _is_secure(_login(accel, https=False, headers={"X-Forwarded-Proto": "https"}))


def test_forwarded_header_is_ignored_without_a_declared_proxy(accel):
    """RFC 7239 ``Forwarded`` is a request header too, so it is gated by the same flag."""
    assert not _is_secure(_login(accel, https=False, headers={"Forwarded": "for=10.0.0.1;proto=https"}))


def test_declared_proxy_makes_forwarded_proto_authoritative(accel, monkeypatch):
    """The supported TLS-terminating deployment: plain HTTP listener, HTTPS at the edge."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")

    assert _is_secure(_login(accel, https=False, headers={"X-Forwarded-Proto": "https"}))


def test_declared_proxy_is_believed_when_it_reports_plain_http(accel, monkeypatch):
    """A trusted hop is trusted in both directions — it knows what the client leg was."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "1")

    assert not _is_secure(_login(accel, https=True, headers={"X-Forwarded-Proto": "http"}))


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("https, http", False),
        ("http, https", True),
        ("http,https", True),
        ("  https  ", True),
    ],
)
def test_last_hop_wins_in_a_comma_list(accel, monkeypatch, header, expected):
    """Each hop appends; only the last element came from the declared proxy."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "yes")

    assert _is_secure(_login(accel, https=False, headers={"X-Forwarded-Proto": header})) is expected


def test_a_second_header_field_from_the_proxy_beats_the_one_the_client_planted(accel, monkeypatch):
    """A hop may append a whole new field instead of extending the list; that one is the proxy's."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")
    planted_then_proxy = [("X-Forwarded-Proto", "http"), ("X-Forwarded-Proto", "https")]

    response = _client(accel, https=False).post("/v1/auth/login", json=LOGIN, headers=planted_then_proxy)

    assert _is_secure(response), "the proxy's own field must win over a client-planted one"


def test_repeated_header_fields_are_ignored_without_a_declared_proxy(accel):
    assert not _is_secure(
        _client(accel, https=False).post(
            "/v1/auth/login",
            json=LOGIN,
            headers=[("X-Forwarded-Proto", "https"), ("X-Forwarded-Proto", "https")],
        )
    )


def test_comma_list_is_ignored_entirely_without_a_declared_proxy(accel):
    """Neither element of a forged list may decide the flag when no proxy is declared."""
    assert not _is_secure(_login(accel, https=False, headers={"X-Forwarded-Proto": "http, https"}))


def test_declared_proxy_reads_forwarded_when_x_forwarded_proto_is_absent(accel, monkeypatch):
    """Fallback for proxies that emit only the standard header; last element again."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")
    headers = {"Forwarded": 'for=203.0.113.7;proto=http, for=10.0.0.1;proto="https"'}

    assert _is_secure(_login(accel, https=False, headers=headers))


def test_x_forwarded_proto_wins_over_forwarded(accel, monkeypatch):
    """Both present: the header every real proxy sets decides, so the two cannot disagree."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")
    headers = {"X-Forwarded-Proto": "https", "Forwarded": "for=10.0.0.1;proto=http"}

    assert _is_secure(_login(accel, https=False, headers=headers))


def test_the_two_forwarding_headers_resolve_to_https_when_they_disagree(accel, monkeypatch):
    """A proxy may write one header and pass the other through from the client untouched.

    Neither precedence order is safe on its own, so the HTTPS reading wins: a wrongly
    ``Secure`` cookie is one the browser drops, while the other way round loses the flag.
    """
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")
    proxy_wrote_forwarded = {"X-Forwarded-Proto": "http", "Forwarded": "for=10.0.0.1;proto=https"}

    assert _is_secure(_login(accel, https=False, headers=proxy_wrote_forwarded))


def test_a_quoted_comma_cannot_hide_the_protocol(accel, monkeypatch):
    """RFC 7239 values may be quoted, and a naive split on ``,`` would cut inside one."""
    monkeypatch.setenv("ADO2GH_TRUSTED_PROXY", "true")
    headers = {"Forwarded": 'for="[2001:db8::1],[2001:db8::2]";proto=https'}

    assert _is_secure(_login(accel, https=False, headers=headers))


def test_the_shipped_launcher_disables_uvicorns_proxy_header_middleware():
    """Uvicorn rewrites the scheme from ``X-Forwarded-Proto`` for trusted peers by default.

    That happens before any route runs, so it would decide the ``Secure`` flag behind the
    back of ``ADO2GH_TRUSTED_PROXY``; the served app must switch it off.
    """
    dockerfile = REPO_ROOT / "services" / "accelerator_api" / "Dockerfile"

    assert "--no-proxy-headers" in dockerfile.read_text(encoding="utf-8")


def test_the_middleware_this_guards_against_really_does_rewrite_the_scheme(accel):
    """Why the launcher flag matters: with the middleware on, the header decides the scheme.

    The app is wrapped here exactly as uvicorn would wrap it, with the caller treated as a
    trusted peer, and the forged header alone flips the cookie the route issues.
    """
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    wrapped = ProxyHeadersMiddleware(accel, trusted_hosts="*")
    client = TestClient(wrapped, base_url="http://testserver")

    response = client.post("/v1/auth/login", json=LOGIN, headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 200, response.text
    assert _is_secure(response), "middleware left on would let the header decide — hence --no-proxy-headers"


def test_logout_clears_the_cookie_under_the_same_rule(accel):
    """``_clear_session_cookie`` shares the decision; a non-Secure delete cannot replace a Secure cookie."""
    client = _client(accel, https=True)
    client.post("/v1/auth/login", json=LOGIN)

    response = client.post("/v1/auth/logout", headers={"X-Forwarded-Proto": "http"})

    assert _is_secure(response), "a forged header must not stop the Secure cookie being cleared"
