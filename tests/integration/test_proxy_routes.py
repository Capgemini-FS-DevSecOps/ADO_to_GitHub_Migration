"""Forwarding and masking behaviour of ``services/accelerator_api/routes/proxy_routes.py``.

COV-DRIFT-001 names this module (380 lines) as one of the largest with no direct
test reference. ``tests/unit/test_gap_008_github_write_proxy_unguarded.py``
proves the *guard* on the write verbs; this proves what the proxy actually
*does* once a call is allowed through — which URL it builds, which query
parameters it adds, how it translates an upstream failure, and that the
platform's own credential never appears in a response or an audit record
(CA-003).

Both upstream clients are ``MagicMock``s, so nothing leaves the process.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import permissions_for
from services.accelerator_api.routes import proxy_routes

# Obviously fake: the value the platform would send upstream, never a real one.
FAKE_PLATFORM_PAT = "ghp_fakeplatformtokenvalue000000000001"


class _Clients:
    """The ADO and GitHub doubles a single test drives the proxy with."""

    def __init__(self) -> None:
        self.ado = MagicMock()
        self.ado.org_url = "https://dev.azure.com/fake-org"
        self.ado._get.return_value = {"value": []}
        self.gh = MagicMock()

    def as_tuple(self) -> tuple:
        return (self.ado, self.gh, "fake-gh-org", MagicMock())


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    """Client for the proxy router alone, with a settable platform identity."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "proxy.db"))
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
    client.login = lambda role: current.__setitem__(  # type: ignore[attr-defined]
        "user", PlatformUser("u1", f"{role.value}1", role, role.value.title()),
    )
    client.login(PlatformRole.ADMIN)
    return client


@pytest.fixture
def clients():
    holder = _Clients()
    with patch.object(proxy_routes, "_get_clients", side_effect=holder.as_tuple):
        yield holder


def _http_error(status: int, payload: object) -> requests.HTTPError:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return requests.HTTPError(f"{status} error", response=response)


# --------------------------------------------------------------------------
# ADO proxy — read-only
# --------------------------------------------------------------------------


def test_ado_get_builds_the_url_from_the_active_org_and_adds_a_default_api_version(
    proxy, clients,
):
    clients.ado._get.return_value = {"count": 2}
    resp = proxy.get("/v1/ado/_apis/projects")
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}
    url = clients.ado._get.call_args.args[0]
    assert url == "https://dev.azure.com/fake-org/_apis/projects"
    assert clients.ado._get.call_args.kwargs["params"] == {"api-version": "7.1"}


def test_ado_get_parses_a_query_string_encoded_into_the_path(proxy, clients):
    """The handler splits the captured path on ``?`` after percent-decoding it."""
    clients.ado._get.return_value = {}
    proxy.get("/v1/ado/_apis/build/builds%3Fdefinitions%3D42%26api-version%3D6.0")
    params = clients.ado._get.call_args.kwargs["params"]
    assert params["definitions"] == "42"
    assert params["api-version"] == "6.0", "a caller-supplied API version was overwritten"
    assert clients.ado._get.call_args.args[0] == (
        "https://dev.azure.com/fake-org/_apis/build/builds"
    )


def test_a_plain_query_string_does_not_reach_the_upstream_call(proxy, clients):
    """Starlette hands the handler the path only, so ``?a=b`` on the URL is dropped.

    Pinned as observed behaviour: the handler's own query parsing only fires for
    a query string percent-encoded into the path segment. The docstring promises
    that "any query string on it is passed along"; that divergence is carried as
    a follow-up rather than fixed here.
    """
    clients.ado._get.return_value = {}
    proxy.get("/v1/ado/_apis/build/builds?definitions=42")
    params = clients.ado._get.call_args.kwargs["params"]
    assert "definitions" not in params
    assert params == {"api-version": "7.1"}


def test_ado_repo_path_is_answered_from_the_repository_lookup(proxy, clients):
    clients.ado.get_repo.return_value = {
        "id": "repo-guid", "name": "payments", "defaultBranch": "refs/heads/main",
        "size": 1024,
    }
    resp = proxy.get("/v1/ado/projects/Contoso%20Core/repos/payments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "repo-guid"
    assert body["name"] == "payments"
    assert body["defaultBranch"] == "refs/heads/main"
    assert body["size"] == 1024, "the full upstream body must survive the merge"
    # The percent-encoded project name is decoded before the lookup.
    assert clients.ado.get_repo.call_args.args == ("Contoso Core", "payments")
    assert not clients.ado._get.called, "the repo short-circuit still made a raw call"


def test_ado_upstream_failure_preserves_the_status_and_surfaces_only_the_message(
    proxy, clients,
):
    clients.ado._get.side_effect = _http_error(
        404, {"message": "project does not exist", "request": f"pat={FAKE_PLATFORM_PAT}"},
    )
    resp = proxy.get("/v1/ado/_apis/projects/missing")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "project does not exist"
    assert FAKE_PLATFORM_PAT not in resp.text, "the upstream request leaked into the error"


def test_ado_failure_with_no_response_becomes_a_502(proxy, clients):
    clients.ado._get.side_effect = requests.HTTPError("connection reset")
    resp = proxy.get("/v1/ado/_apis/projects")
    assert resp.status_code == 502


def test_ado_repo_lookup_failure_preserves_the_upstream_status(proxy, clients):
    clients.ado.get_repo.side_effect = _http_error(403, {"message": "forbidden"})
    resp = proxy.get("/v1/ado/projects/Contoso/repos/payments")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "forbidden"


def test_the_ado_proxy_exposes_no_write_verb(proxy, clients):
    """ADO is never mutated on the platform's behalf."""
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        resp = proxy.request(method, "/v1/ado/_apis/git/repositories/x")
        assert resp.status_code == 405, f"{method} /v1/ado/... is routable"


# --------------------------------------------------------------------------
# GitHub proxy — reads
# --------------------------------------------------------------------------


def test_github_repo_path_is_answered_from_the_repository_lookup(proxy, clients):
    clients.gh.get_repo.return_value = {"full_name": "fake-gh-org/payments"}
    resp = proxy.get("/v1/github/repos/fake-gh-org/payments")
    assert resp.status_code == 200
    assert resp.json() == {"full_name": "fake-gh-org/payments"}
    assert clients.gh.get_repo.call_args.args == ("fake-gh-org", "payments")
    assert not clients.gh._get.called


def test_github_get_forwards_the_endpoint_with_one_leading_slash(proxy, clients):
    clients.gh._get.return_value = [{"name": "main"}]
    resp = proxy.get("/v1/github/repos/fake-gh-org/payments/branches?per_page=100")
    assert resp.status_code == 200
    assert resp.json() == [{"name": "main"}]
    assert clients.gh._get.call_args.args[0] == "/repos/fake-gh-org/payments/branches", (
        "the query string must be stripped from the endpoint the client is given"
    )


def test_github_read_needs_only_the_operate_capability(proxy, clients):
    """A read is not a live-execution act, so an operator may take it (GAP-008)."""
    proxy.login(PlatformRole.OPERATOR)
    clients.gh._get.return_value = {"login": "fake-gh-org"}
    assert proxy.get("/v1/github/orgs/fake-gh-org").status_code == 200


def test_github_upstream_failure_preserves_the_status(proxy, clients):
    clients.gh._get.side_effect = _http_error(422, {"message": "Validation Failed"})
    resp = proxy.get("/v1/github/orgs/fake-gh-org")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Validation Failed"


def test_github_failure_with_an_unparseable_body_falls_back_to_the_error_text(proxy, clients):
    response = MagicMock()
    response.status_code = 500
    response.json.side_effect = ValueError("not json")
    clients.gh._get.side_effect = requests.HTTPError("500 upstream", response=response)
    resp = proxy.get("/v1/github/orgs/fake-gh-org")
    assert resp.status_code == 500
    assert "500 upstream" in resp.json()["detail"]


# --------------------------------------------------------------------------
# GitHub proxy — writes
# --------------------------------------------------------------------------


def test_post_forwards_the_body_and_returns_the_upstream_json(proxy, clients):
    clients.gh._post.return_value = {"id": 7, "name": "new-repo"}
    resp = proxy.post("/v1/github/orgs/fake-gh-org/repos", json={"name": "new-repo"})
    assert resp.status_code == 200
    assert resp.json() == {"id": 7, "name": "new-repo"}
    endpoint, body = clients.gh._post.call_args.args
    assert endpoint == "/orgs/fake-gh-org/repos"
    assert body == {"name": "new-repo"}


def test_patch_forwards_the_body(proxy, clients):
    clients.gh._patch.return_value = {"archived": True}
    resp = proxy.patch("/v1/github/repos/fake-gh-org/payments", json={"archived": True})
    assert resp.status_code == 200
    assert clients.gh._patch.call_args.args == (
        "/repos/fake-gh-org/payments", {"archived": True},
    )


def test_a_non_object_write_body_is_forwarded_as_an_empty_object(proxy, clients):
    clients.gh._post.return_value = {}
    proxy.post("/v1/github/orgs/fake-gh-org/repos", json=["not", "an", "object"])
    assert clients.gh._post.call_args.args[1] == {}


def test_put_with_an_empty_upstream_body_returns_a_status_summary(proxy, clients):
    clients.gh._put.return_value = MagicMock(content=b"", status_code=204, ok=True)
    resp = proxy.put(
        "/v1/github/repos/fake-gh-org/payments/collaborators/someone",
        json={"permission": "push"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status_code": 204, "ok": True}


def test_delete_with_an_empty_upstream_body_returns_a_status_summary(proxy, clients):
    clients.gh._delete.return_value = MagicMock(content=b"", status_code=204, ok=True)
    resp = proxy.delete("/v1/github/repos/fake-gh-org/payments")
    assert resp.status_code == 200
    assert resp.json() == {"status_code": 204, "ok": True}
    assert clients.gh._delete.call_args.args[0] == "/repos/fake-gh-org/payments"


def test_delete_returning_a_json_body_passes_it_through(proxy, clients):
    response = MagicMock(content=b'{"message":"gone"}', status_code=200, ok=True)
    response.json.return_value = {"message": "gone"}
    clients.gh._delete.return_value = response
    assert proxy.delete("/v1/github/repos/fake-gh-org/payments").json() == {"message": "gone"}


# --------------------------------------------------------------------------
# Audit and masking (CA-003, CA-004)
# --------------------------------------------------------------------------


def test_the_write_audit_records_verb_endpoint_and_actor_and_no_payload(proxy, clients):
    clients.gh._post.return_value = {}
    with patch(
        "ado2gh.api.profile_governance.write_profile_audit",
    ) as audit:
        proxy.post(
            "/v1/github/orgs/fake-gh-org/repos",
            json={"name": "new-repo", "token": FAKE_PLATFORM_PAT},
        )
    assert audit.called, "an allowed GitHub write left no audit record"
    payload = audit.call_args.kwargs["payload"]
    assert payload["method"] == "POST"
    assert payload["endpoint"] == "/orgs/fake-gh-org/repos"
    assert payload["role"] == PlatformRole.ADMIN.value
    assert "body" not in payload, "the forwarded body reached the audit record"
    assert FAKE_PLATFORM_PAT not in str(payload)


def test_the_audited_endpoint_never_carries_the_query_string(proxy, clients):
    """A credential smuggled into a query string must not be audited (CWE-598)."""
    clients.gh._delete.return_value = MagicMock(content=b"", status_code=204, ok=True)
    with patch("ado2gh.api.profile_governance.write_profile_audit") as audit:
        proxy.delete(f"/v1/github/repos/fake-gh-org/payments?access_token={FAKE_PLATFORM_PAT}")
    assert audit.call_args.kwargs["payload"]["endpoint"] == "/repos/fake-gh-org/payments"
    assert FAKE_PLATFORM_PAT not in str(audit.call_args.kwargs["payload"])


def test_a_read_is_not_audited(proxy, clients):
    """The asymmetry is deliberate: a GET changes nothing, so it is not recorded."""
    clients.gh._get.return_value = {}
    with patch("ado2gh.api.profile_governance.write_profile_audit") as audit:
        proxy.get("/v1/github/orgs/fake-gh-org")
    assert not audit.called


def test_a_refused_write_is_never_forwarded_and_never_audited(proxy, clients):
    proxy.login(PlatformRole.OPERATOR)
    with patch("ado2gh.api.profile_governance.write_profile_audit") as audit:
        resp = proxy.delete("/v1/github/repos/fake-gh-org/payments")
    assert resp.status_code in (401, 403)
    assert not clients.gh._delete.called
    assert not audit.called, "a refused write was recorded as if it had happened"
