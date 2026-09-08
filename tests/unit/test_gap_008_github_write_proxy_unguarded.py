"""GAP-008 (GAP-ACC-02) — the GitHub write proxy is documented as read-only,
guarded only by ``require_operate``, and writes nothing to the audit log.

Reproduction described by the gap register: the module docstring of
``services/accelerator_api/routes/proxy_routes.py`` says "Read-only ADO and
GitHub API proxies for the migration agent", yet the module registers
``@router.post``, ``@router.patch``, ``@router.put`` and ``@router.delete`` on
``/v1/github/{path:path}``, each forwarding an arbitrary path and body to
GitHub. The single shared helper ``_proxy_github_request`` calls
``require_operate(request)`` as its only guard — no live-execution approval
check, no ``AuditWriter`` call, and no path allowlist for the write verbs (the
only path inspection is a ``GET``-only branch on ``clean.startswith("repos/")``).

So a caller holding nothing but the routine operate permission can issue any
GitHub REST write the platform token can perform — including
``DELETE /repos/{org}/{repo}`` — through a route whose own documentation says it
is read-only, and the platform keeps no record that it happened.

These tests fail on current code and pass once GitHub writes through the proxy
are gated at their choke point (``_proxy_github_request``) by the same
live-execution approval the rest of the platform uses, and once an accepted
write leaves an audit event behind.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.audit import AuditWriter
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import permissions_for
from services.accelerator_api.routes import proxy_routes

# Every write verb the proxy exposes on /v1/github/{path:path}, aimed at a
# destructive or state-changing GitHub endpoint.
GITHUB_WRITES = [
    ("DELETE", "/v1/github/repos/acme/production-service", None),
    ("POST", "/v1/github/orgs/acme/repos", {"name": "new-repo"}),
    ("PATCH", "/v1/github/repos/acme/production-service", {"archived": True}),
    ("PUT", "/v1/github/repos/acme/production-service/collaborators/attacker",
     {"permission": "admin"}),
]


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    """Client for the proxy router with platform auth enabled.

    ``proxy.login(role)`` sets the platform user for subsequent requests. The
    GitHub client is a ``MagicMock``; every call it records is a request that
    was actually forwarded to GitHub.
    """
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap008.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))

    current: dict = {"user": None}

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request, call_next):
        user = current["user"]
        if user is not None:
            request.state.platform_user = user
            request.state.permissions = permissions_for(user.role)
        return await call_next(request)

    app.include_router(proxy_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    def login(role: PlatformRole):
        current["user"] = PlatformUser(
            "u1", f"{role.value}1", role, role.value.title(),
        )

    client.login = login  # type: ignore[attr-defined]
    return client


def _github_mock() -> MagicMock:
    gh = MagicMock()
    for verb in ("_post", "_patch", "_put", "_delete"):
        getattr(gh, verb).return_value = MagicMock(
            content=b"", status_code=204, ok=True,
        )
    return gh


def _write_calls(gh: MagicMock) -> list[str]:
    return [
        verb for verb in ("_post", "_patch", "_put", "_delete")
        if getattr(gh, verb).called
    ]


@pytest.mark.parametrize(
    "method,path,body", GITHUB_WRITES,
    ids=[f"{m}-{p.split('/v1/github/')[1]}" for m, p, _ in GITHUB_WRITES],
)
def test_github_write_not_reachable_on_operate_permission_alone(proxy, method, path, body):
    """The routine operate permission alone must not forward a GitHub write."""
    proxy.login(PlatformRole.OPERATOR)
    gh = _github_mock()

    with patch.object(
        proxy_routes, "_get_clients",
        side_effect=lambda: (MagicMock(), gh, "acme", MagicMock()),
    ):
        resp = proxy.request(method, path, json=body)

    assert not _write_calls(gh), (
        f"{method} {path} was forwarded to GitHub ({_write_calls(gh)}) for a caller "
        f"holding only the operate permission — no live-execution approval required"
    )
    assert resp.status_code in (401, 403, 405), (
        f"{method} {path} accepted an unapproved GitHub write (HTTP {resp.status_code})"
    )


@pytest.mark.parametrize(
    "method,path,body", GITHUB_WRITES,
    ids=[f"{m}-{p.split('/v1/github/')[1]}" for m, p, _ in GITHUB_WRITES],
)
def test_github_write_is_audited(proxy, method, path, body):
    """Any GitHub write the proxy does allow leaves an audit record behind."""
    proxy.login(PlatformRole.ADMIN)
    gh = _github_mock()

    with patch.object(AuditWriter, "write", return_value="aud_test") as audit_write:
        with patch.object(
            proxy_routes, "_get_clients",
            side_effect=lambda: (MagicMock(), gh, "acme", MagicMock()),
        ):
            resp = proxy.request(method, path, json=body)

    if not _write_calls(gh):
        pytest.skip(f"{method} {path} was refused (HTTP {resp.status_code}) — nothing to audit")

    assert audit_write.called, (
        f"{method} {path} was forwarded to GitHub with no audit event recorded"
    )
