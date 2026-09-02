"""Read-only ADO and GitHub API proxies for the migration agent."""
from __future__ import annotations

from urllib.parse import parse_qs, unquote

import requests
from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.platform_rbac import require_operate
from services.accelerator_api.routes.migrate_routes import _get_clients

router = APIRouter(tags=["api-proxy"])


def _http_error(exc: requests.HTTPError) -> HTTPException:
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
def proxy_ado_get(path: str, request: Request):
    """Proxy read-only GET requests to Azure DevOps."""
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
    clean = unquote(path.strip("/"))
    endpoint = f"/{clean}" if clean else "/"
    return endpoint.split("?", 1)[0]


def _proxy_github_request(method: str, path: str, request: Request, body: dict | None = None):
    """Proxy GitHub REST requests (GET read-only; POST/PATCH/PUT/DELETE for executor writes)."""
    require_operate(request)
    _, gh, _, _ = _get_clients()
    clean = unquote(path.strip("/"))
    method_upper = method.upper()

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
def proxy_github_get(path: str, request: Request):
    """Proxy read-only GET requests to GitHub."""
    return _proxy_github_request("GET", path, request)


@router.post("/v1/github/{path:path}")
async def proxy_github_post(path: str, request: Request):
    """Proxy POST requests to GitHub (executor write access)."""
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    return _proxy_github_request("POST", path, request, body=body)


@router.patch("/v1/github/{path:path}")
async def proxy_github_patch(path: str, request: Request):
    """Proxy PATCH requests to GitHub (executor write access)."""
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    return _proxy_github_request("PATCH", path, request, body=body)


@router.put("/v1/github/{path:path}")
async def proxy_github_put(path: str, request: Request):
    """Proxy PUT requests to GitHub (executor write access)."""
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    return _proxy_github_request("PUT", path, request, body=body)


@router.delete("/v1/github/{path:path}")
def proxy_github_delete(path: str, request: Request):
    """Proxy DELETE requests to GitHub (executor write access)."""
    return _proxy_github_request("DELETE", path, request)
