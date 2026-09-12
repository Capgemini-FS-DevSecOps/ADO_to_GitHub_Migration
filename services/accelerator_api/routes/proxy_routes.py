"""ADO and GitHub API proxies for the migration agent.

The ADO proxy is read-only. The GitHub proxy also forwards the executor's write
verbs, so every write goes through the same live-execution capability the rest of
the platform requires and leaves an audit event behind (GAP-008).
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote

import requests
from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.platform_rbac import require_approve_live_execution, require_operate
from services.accelerator_api.routes.migrate_guard import active_profile_id
from services.accelerator_api.routes.migrate_routes import _get_clients

if TYPE_CHECKING:  # pragma: no cover - types only
    from ado2gh.auth.models import PlatformUser

router = APIRouter(tags=["api-proxy"])

# Verbs this proxy forwards that mutate GitHub — up to DELETE /repos/{org}/{repo}.
_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _http_error(exc: requests.HTTPError) -> HTTPException:
    """Translate an upstream HTTP failure into the error this proxy returns.

    The upstream status code is preserved so the caller sees the same 404, 403 or
    422 the real API produced; a failure with no response at all becomes a 502.
    Only the upstream ``message`` field is surfaced, never the request that
    carried the platform's credentials.

    Args:
        exc: The error raised by the underlying ADO or GitHub client.

    Returns:
        An ``HTTPException`` carrying the upstream status code and, when the
        upstream body was JSON with a ``message`` field, that message as the
        detail; otherwise the string form of the original error.
    """
    status = exc.response.status_code if exc.response is not None else 502
    detail = str(exc)
    try:
        payload = exc.response.json()
        if isinstance(payload, dict) and payload.get("message"):
            detail = str(payload["message"])
    except Exception:
        pass
    return HTTPException(status_code=status, detail=detail)


@router.get("/v1/ado/{path:path}")
def proxy_ado_get(path: str, request: Request) -> object:
    """Read from the Azure DevOps REST API through the platform's own credentials.

    Everything after ``/v1/ado/`` is forwarded as the ADO path, relative to the
    active profile's organisation URL, and any query string on it is passed
    along. ``api-version=7.1`` is added when the caller supplies no version.
    ``projects/{project}/repos/{repo}`` is answered from the repository lookup
    the platform already uses, so the response carries ``id``, ``name`` and
    ``defaultBranch`` alongside the full upstream body.

    This proxy is read-only by design: there is no write counterpart, and ADO is
    never mutated on the platform's behalf.

    Args:
        path: The ADO REST path to read, optionally with a query string.
        request: The incoming request, used for the capability check.

    Returns:
        The decoded upstream JSON body, unchanged.

    Raises:
        HTTPException: 401 or 403 when the caller lacks ``can_operate``, 400 when
            no migration profile is active, or the upstream status code when ADO
            rejects the read.
    """
    require_operate(request)
    ado, _, _, _ = _get_clients()
    clean = unquote(path.strip("/"))

    if clean.startswith("projects/") and "/repos/" in clean:
        rest = clean[len("projects/") :]
        project, _, repo = rest.partition("/repos/")
        repo = repo.split("?", 1)[0]
        if project and repo:
            try:
                data = ado.get_repo(project, repo)
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "defaultBranch": data.get("defaultBranch"),
                    **data,
                }
            except requests.HTTPError as exc:
                raise _http_error(exc) from exc

    base = ado.org_url.rstrip("/")
    path_part, _, query = clean.partition("?")
    url = f"{base}/{path_part}"
    params: dict[str, str] = {}
    if query:
        for key, values in parse_qs(query).items():
            if values:
                params[key] = values[0]
    if "api-version" not in params and "api-version" not in path_part:
        params["api-version"] = "7.1"
    try:
        return ado._get(url, params=params or None)
    except requests.HTTPError as exc:
        raise _http_error(exc) from exc


def _github_endpoint(path: str) -> str:
    """Normalise a proxied path into the GitHub API endpoint to call.

    Args:
        path: The raw, still percent-encoded path captured from the URL.

    Returns:
        The decoded path with a single leading slash and the query string
        stripped, or ``"/"`` when nothing was supplied.
    """
    clean = unquote(path.strip("/"))
    endpoint = f"/{clean}" if clean else "/"
    return endpoint.split("?", 1)[0]


def _audit_github_write(user: PlatformUser | None, method: str, endpoint: str) -> None:
    """Record a GitHub write before it is forwarded (CA-004).

    Verb, endpoint and actor only: the forwarded body and the platform's own
    ``Authorization`` header must never reach an audit row (CA-003).

    Args:
        user: The identity that passed the live-execution check.
        method: The upper-cased HTTP verb about to be forwarded.
        endpoint: The normalised GitHub endpoint, query string already removed.
    """
    from ado2gh.api.profile_governance import write_profile_audit

    write_profile_audit(
        "accelerator.github_proxy.write",
        profile_id=active_profile_id() or "_platform",
        actor=getattr(user, "username", "") or "",
        payload={
            "method": method,
            "endpoint": endpoint,
            "role": user.role.value if user else None,
        },
    )


def _proxy_github_request(
    method: str, path: str, request: Request, body: dict | None = None,
) -> object:
    """Forward one GitHub REST call, guarding reads and writes differently.

    The guard is deliberately asymmetric (GAP-008). A ``GET`` only needs the
    routine ``can_operate`` capability and is not audited, because it changes
    nothing. Every verb in ``_WRITE_METHODS`` instead needs
    ``can_approve_live_execution`` and is written to the audit log *before* the
    call leaves the process, so an irreversible GitHub mutation can never happen
    without a recorded actor. Do not harmonise the two paths.

    ``GET repos/{org}/{repo}`` short-circuits to the platform's repository
    lookup; everything else is passed through verbatim. ``PUT`` and ``DELETE``
    fall back to a status summary when the upstream response has no JSON body.

    Args:
        method: HTTP verb to forward; matched case-insensitively.
        path: The raw path captured after ``/v1/github/``.
        request: The incoming request, used for the capability check and audit.
        body: JSON body to forward on a write verb. Ignored for ``GET`` and
            ``DELETE``, and sent as an empty object when omitted.

    Returns:
        The decoded upstream JSON — an object or an array, depending on the
        endpoint — or, for a ``PUT``/``DELETE`` that returned no JSON body, a
        mapping with the upstream ``status_code`` and an ``ok`` flag.

    Raises:
        HTTPException: 401 or 403 when the caller lacks the capability the verb
            requires, 405 for a verb this proxy does not forward, 400 when no
            migration profile is active, or the upstream status code when GitHub
            rejects the call.
    """
    method_upper = method.upper()
    if method_upper in _WRITE_METHODS:
        # A write here is an irreversible GitHub mutation, so it takes the
        # live-execution capability rather than the routine operate permission,
        # and it is recorded before it leaves the process.
        user = require_approve_live_execution(request)
        _audit_github_write(user, method_upper, _github_endpoint(path))
    else:
        require_operate(request)
    _, gh, _, _ = _get_clients()
    clean = unquote(path.strip("/"))

    if method_upper == "GET" and clean.startswith("repos/"):
        parts = clean.split("/")
        if len(parts) == 3:
            org, repo = parts[1], parts[2].split("?", 1)[0]
            try:
                return gh.get_repo(org, repo)
            except requests.HTTPError as exc:
                raise _http_error(exc) from exc

    endpoint = _github_endpoint(path)
    try:
        if method_upper == "GET":
            return gh._get(endpoint)
        if method_upper == "POST":
            return gh._post(endpoint, body or {})
        if method_upper == "PATCH":
            return gh._patch(endpoint, body or {})
        if method_upper == "PUT":
            response = gh._put(endpoint, body or {})
            if response.content:
                try:
                    return response.json()
                except Exception:
                    return {"status_code": response.status_code, "ok": response.ok}
            return {"status_code": response.status_code, "ok": response.ok}
        if method_upper == "DELETE":
            response = gh._delete(endpoint)
            if response.content:
                try:
                    return response.json()
                except Exception:
                    pass
            return {"status_code": response.status_code, "ok": response.ok}
        raise HTTPException(status_code=405, detail=f"Unsupported method: {method}")
    except requests.HTTPError as exc:
        raise _http_error(exc) from exc


@router.get("/v1/github/{path:path}")
def proxy_github_get(path: str, request: Request) -> object:
    """Read from the GitHub REST API through the platform's own credentials.

    Everything after ``/v1/github/`` is forwarded as the GitHub API path.
    ``repos/{org}/{repo}`` is answered from the platform's repository lookup.
    Being a read, this needs only the routine operate capability and is not
    audited — unlike the write verbs on the same path (GAP-008).

    Args:
        path: The GitHub REST path to read.
        request: The incoming request, used for the capability check.

    Returns:
        The decoded upstream JSON body — an object for most endpoints, an array
        for the listing endpoints — unchanged.

    Raises:
        HTTPException: 401 or 403 when the caller lacks ``can_operate``, 400 when
            no migration profile is active, or the upstream status code when
            GitHub rejects the read.
    """
    return _proxy_github_request("GET", path, request)


@router.post("/v1/github/{path:path}")
async def proxy_github_post(path: str, request: Request) -> object:
    """Create a GitHub resource through the platform's own credentials.

    A write, so it requires the live-execution approval capability rather than
    the operate capability the ``GET`` on this path uses, and the verb, endpoint
    and actor are recorded in the audit log before the call is forwarded
    (GAP-008). The request body is forwarded as-is; a non-object body, or none at
    all, is sent as an empty object.

    Args:
        path: The GitHub REST path to post to.
        request: The incoming request, used for the capability check, the audit
            record and the JSON body.

    Returns:
        The decoded upstream JSON body, unchanged.

    Raises:
        HTTPException: 401 when the request carries no identity, 403 when the
            caller cannot approve live execution, 400 when no migration profile
            is active, or the upstream status code when GitHub rejects the write.
    """
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    return _proxy_github_request("POST", path, request, body=body)


@router.patch("/v1/github/{path:path}")
async def proxy_github_patch(path: str, request: Request) -> object:
    """Update a GitHub resource through the platform's own credentials.

    A write, so it requires the live-execution approval capability rather than
    the operate capability the ``GET`` on this path uses, and the verb, endpoint
    and actor are recorded in the audit log before the call is forwarded
    (GAP-008). The request body is forwarded as-is; a non-object body, or none at
    all, is sent as an empty object.

    Args:
        path: The GitHub REST path to patch.
        request: The incoming request, used for the capability check, the audit
            record and the JSON body.

    Returns:
        The decoded upstream JSON body, unchanged.

    Raises:
        HTTPException: 401 when the request carries no identity, 403 when the
            caller cannot approve live execution, 400 when no migration profile
            is active, or the upstream status code when GitHub rejects the write.
    """
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    return _proxy_github_request("PATCH", path, request, body=body)


@router.put("/v1/github/{path:path}")
async def proxy_github_put(path: str, request: Request) -> object:
    """Replace a GitHub resource through the platform's own credentials.

    A write, so it requires the live-execution approval capability rather than
    the operate capability the ``GET`` on this path uses, and the verb, endpoint
    and actor are recorded in the audit log before the call is forwarded
    (GAP-008). The request body is forwarded as-is; a non-object body, or none at
    all, is sent as an empty object.

    Args:
        path: The GitHub REST path to put to.
        request: The incoming request, used for the capability check, the audit
            record and the JSON body.

    Returns:
        The decoded upstream JSON body, or a mapping with the upstream
        ``status_code`` and an ``ok`` flag for the endpoints that answer a
        successful put with no body.

    Raises:
        HTTPException: 401 when the request carries no identity, 403 when the
            caller cannot approve live execution, 400 when no migration profile
            is active, or the upstream status code when GitHub rejects the write.
    """
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    return _proxy_github_request("PUT", path, request, body=body)


@router.delete("/v1/github/{path:path}")
def proxy_github_delete(path: str, request: Request) -> object:
    """Delete a GitHub resource through the platform's own credentials.

    The most destructive verb this proxy forwards — up to deleting a repository —
    so it requires the live-execution approval capability rather than the operate
    capability the ``GET`` on this path uses, and the verb, endpoint and actor are
    recorded in the audit log before the call is forwarded (GAP-008). No request
    body is forwarded.

    Args:
        path: The GitHub REST path to delete.
        request: The incoming request, used for the capability check and the
            audit record.

    Returns:
        The decoded upstream JSON body, or a mapping with the upstream
        ``status_code`` and an ``ok`` flag for the usual empty 204 response.

    Raises:
        HTTPException: 401 when the request carries no identity, 403 when the
            caller cannot approve live execution, 400 when no migration profile
            is active, or the upstream status code when GitHub rejects the
            deletion.
    """
    return _proxy_github_request("DELETE", path, request)
