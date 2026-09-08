"""GAP-007 (GAP-ACC-01) — nine ``/v1/migrate/*`` feature routes perform live
mutations with no RBAC, approval, or audit.

Reproduction described by the gap register: the router in
``services/accelerator_api/routes/migrate_routes.py`` is declared with no
``dependencies=``, and a repo-wide grep of that file for ``require_operate``,
``platform_rbac``, ``live_approval``, ``AuditWriter`` or ``audit`` returns zero
matches. So every one of the nine POST handlers (git-mirror, pipeline-convert,
secret-provision, service-connection, boards, test-plans, artifacts, wiki,
branch-policies) calls its scope handler for any caller at all. Meanwhile the
sibling ``POST /v1/migrate`` in ``services/accelerator_api/main.py`` *does*
enforce ``operator_requires_live_approval`` and parks the request in the
approval queue with HTTP 403 ``awaiting_approval`` — these nine bypass the gate
their own sibling enforces.

With platform auth switched on, a ``"dry_run": false`` POST to any of the nine
therefore still reaches ``_get_clients()`` and mutates GitHub, whether the
caller is unauthenticated or is a plain OPERATOR who has never been approved for
live execution, and no audit event is written for the run.

These tests fail on current code and pass once the nine routes are guarded at
their choke point (the router, or a shared dependency) with the same
authentication + live-approval contract as ``POST /v1/migrate``, and once a live
run records an audit event.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.audit.audit import AuditWriter
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import permissions_for
from ado2gh.core.scopes.base import ScopeResult
from services.accelerator_api.routes import migrate_routes

# The nine live-mutation routes, each with a body its request model accepts and
# ``dry_run`` explicitly false so the call is unambiguously a live mutation.
LIVE_CALLS = [
    ("/v1/migrate/git-mirror", {
        "project": "Proj", "repo_name": "repo",
        "github_org": "acme", "github_repo": "repo", "dry_run": False,
    }),
    ("/v1/migrate/pipeline-convert", {"repo": "Proj/repo", "dry_run": False}),
    ("/v1/migrate/secret-provision", {
        "github_org": "acme", "github_repo": "repo",
        "secret_name": "AZURE_DEVOPS_PAT",
        "secret_value": "fake-pat-not-a-real-credential", "dry_run": False,
    }),
    ("/v1/migrate/service-connection", {
        "project": "Proj", "connection_name": "conn",
        "github_org": "acme", "github_repo": "repo", "dry_run": False,
    }),
    ("/v1/migrate/boards", {
        "project": "Proj", "github_org": "acme",
        "github_repo": "repo", "dry_run": False,
    }),
    ("/v1/migrate/test-plans", {
        "project": "Proj", "github_org": "acme",
        "github_repo": "repo", "dry_run": False,
    }),
    ("/v1/migrate/artifacts", {
        "project": "Proj", "feed_name": "feed",
        "github_org": "acme", "package_type": "npm", "dry_run": False,
    }),
    ("/v1/migrate/wiki", {
        "project": "Proj", "wiki_name": "wiki",
        "github_org": "acme", "github_repo": "repo", "dry_run": False,
    }),
    ("/v1/migrate/branch-policies", {
        "project": "Proj", "repo_name": "repo",
        "github_org": "acme", "github_repo": "repo", "dry_run": False,
    }),
]


@pytest.fixture
def caller(tmp_path, monkeypatch):
    """Client for the migrate router with platform auth enabled.

    ``caller.login(role)`` sets the platform user for subsequent requests;
    ``caller.login(None)`` (the default) leaves the request unauthenticated.
    """
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap007.db"))
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

    app.include_router(migrate_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    def login(role: PlatformRole | None):
        current["user"] = (
            None if role is None
            else PlatformUser("u1", f"{role.value}1", role, role.value.title())
        )

    client.login = login  # type: ignore[attr-defined]
    return client


@pytest.mark.parametrize("path,payload", LIVE_CALLS, ids=[c[0] for c in LIVE_CALLS])
def test_live_migrate_route_rejects_unauthenticated_caller(caller, path, payload):
    """No live migration runs for a caller the platform never authenticated."""
    with patch.object(migrate_routes, "_get_clients") as get_clients:
        get_clients.side_effect = lambda: (MagicMock(), MagicMock(), "acme", MagicMock())
        with patch.object(migrate_routes, "_load_global_cfg", return_value={}):
            with patch.object(migrate_routes, "_state_db", return_value=MagicMock()):
                resp = caller.post(path, json=payload)

    assert not get_clients.called, (
        f"{path} started live migration work for an unauthenticated caller "
        f"(HTTP {resp.status_code})"
    )
    assert resp.status_code in (401, 403), (
        f"{path} accepted an unauthenticated live migration (HTTP {resp.status_code})"
    )


@pytest.mark.parametrize("path,payload", LIVE_CALLS, ids=[c[0] for c in LIVE_CALLS])
def test_live_migrate_route_requires_live_approval_for_operator(caller, path, payload):
    """An unapproved OPERATOR gets the same 403 the sibling POST /v1/migrate gives."""
    caller.login(PlatformRole.OPERATOR)
    with patch.object(migrate_routes, "_get_clients") as get_clients:
        get_clients.side_effect = lambda: (MagicMock(), MagicMock(), "acme", MagicMock())
        with patch.object(migrate_routes, "_load_global_cfg", return_value={}):
            with patch.object(migrate_routes, "_state_db", return_value=MagicMock()):
                resp = caller.post(path, json=payload)

    assert not get_clients.called, (
        f"{path} started live migration work for an OPERATOR with no "
        f"live-execution approval (HTTP {resp.status_code})"
    )
    assert resp.status_code == 403, (
        f"{path} did not park the unapproved OPERATOR's live run in the "
        f"approval queue (HTTP {resp.status_code})"
    )


def test_live_migrate_route_writes_an_audit_event(caller):
    """A live migration that is allowed to proceed leaves an audit record behind."""
    caller.login(PlatformRole.ADMIN)
    path, payload = LIVE_CALLS[0]

    with patch.object(AuditWriter, "write", return_value="aud_test") as audit_write:
        with patch.object(migrate_routes, "_get_clients") as get_clients:
            get_clients.side_effect = lambda: (
                MagicMock(), MagicMock(), "acme", MagicMock(),
            )
            with patch.object(
                migrate_routes, "_load_global_cfg",
                return_value={"migration_strategy": "mirror"},
            ):
                with patch.object(migrate_routes, "_state_db", return_value=MagicMock()):
                    with patch("ado2gh.core.scopes.git_scope.GitScopeHandler") as handler:
                        handler.return_value.migrate.return_value = ScopeResult(
                            stats={"dry_run": False, "strategy": "mirror", "branches": 1},
                            failed=0,
                        )
                        resp = caller.post(path, json=payload)

    assert resp.status_code == 200, f"live migration failed to run: {resp.status_code}"
    assert audit_write.called, (
        f"{path} completed a live migration without writing an audit event"
    )
