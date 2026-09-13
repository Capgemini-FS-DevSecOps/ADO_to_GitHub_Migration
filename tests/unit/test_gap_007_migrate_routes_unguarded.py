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

GAP-065 is a defect in that guard: it decided dry-run from the raw body with
``bool(...)``, so every string spelling of false — the shapes an HTML form, a
query-string-derived body or a shell client sends — read as a dry run to the
guard while pydantic parsed the very same value to ``False`` for the handler,
which then ran live. The guard must derive its flag through the parse the
request models use, and reject a value neither of them can read.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.audit import AuditWriter
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import permissions_for
from ado2gh.core.scopes.base import ScopeResult
from services.accelerator_api.routes import migrate_routes
from services.accelerator_api.routes.migrate_routes_models import GitMirrorRequest

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


# Every spelling pydantic reads as False and ``bool()`` reads as True. Each one is
# therefore a live migration that the guard used to wave through as a dry run.
FALSE_SPELLINGS = ["false", "False", "0", "no", "off"]


@pytest.mark.parametrize("spelling", FALSE_SPELLINGS)
def test_string_spelled_live_run_hits_the_live_gate(caller, spelling):
    """A live run spelled as a string is still a live run to the guard (GAP-065)."""
    path, payload = LIVE_CALLS[0]
    body = {**payload, "dry_run": spelling}
    assert GitMirrorRequest(**body).dry_run is False, (
        f"pydantic no longer reads {spelling!r} as False; the premise of this test moved"
    )

    with patch.object(migrate_routes, "_get_clients") as get_clients:
        get_clients.side_effect = lambda: (MagicMock(), MagicMock(), "acme", MagicMock())
        with patch.object(migrate_routes, "_load_global_cfg", return_value={}):
            with patch.object(migrate_routes, "_state_db", return_value=MagicMock()):
                resp = caller.post(path, json=body)

    assert not get_clients.called, (
        f"{path} started live migration work for an unauthenticated caller who spelled "
        f"dry_run {spelling!r} (HTTP {resp.status_code}); the handler reads that value as "
        f"False while the guard read it as a dry run"
    )
    assert resp.status_code in (401, 403), (
        f"{path} accepted an unauthenticated live migration spelled dry_run={spelling!r} "
        f"(HTTP {resp.status_code})"
    )


def test_unreadable_dry_run_is_rejected_rather_than_guessed(caller):
    """A value neither the guard nor the model can read is a 422, not a guess."""
    path, payload = LIVE_CALLS[0]

    with patch.object(migrate_routes, "_get_clients") as get_clients:
        get_clients.side_effect = lambda: (MagicMock(), MagicMock(), "acme", MagicMock())
        resp = caller.post(path, json={**payload, "dry_run": "maybe"})

    assert not get_clients.called, f"{path} ran migration work for an unreadable dry_run"
    assert resp.status_code == 422, (
        f"{path} answered {resp.status_code} to an unreadable dry_run instead of "
        f"rejecting it outright"
    )


def test_boolean_dry_run_still_bypasses_the_live_gate(caller):
    """Reversible work stays permissive: a real dry run needs no identity (CA-001)."""
    path, payload = LIVE_CALLS[0]

    with patch.object(migrate_routes, "_get_clients") as get_clients:
        get_clients.side_effect = lambda: (MagicMock(), MagicMock(), "acme", MagicMock())
        with patch.object(
            migrate_routes, "_load_global_cfg", return_value={"migration_strategy": "mirror"},
        ):
            with patch.object(migrate_routes, "_state_db", return_value=MagicMock()):
                with patch("ado2gh.core.scopes.git_scope.GitScopeHandler") as handler:
                    handler.return_value.migrate.return_value = ScopeResult(
                        stats={"dry_run": True, "strategy": "mirror"}, failed=0,
                    )
                    resp = caller.post(path, json={**payload, "dry_run": True})

    assert resp.status_code == 200, (
        f"{path} refused an anonymous dry run (HTTP {resp.status_code}); the guard must "
        f"only gate irreversible work"
    )
    assert get_clients.called, f"{path} never reached the dry-run preview"
