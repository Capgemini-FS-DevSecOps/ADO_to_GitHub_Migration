"""Validate ADO and GitHub credentials for the settings UI."""
from __future__ import annotations

from typing import Any

import requests

RECOMMENDED_GH_SCOPES = {"repo", "read:org", "admin:org"}


def validate_github_token(token: str, gh_org: str = "") -> dict[str, Any]:
    """Validate a GitHub PAT — Jenkins-style test connection."""
    if not token or token.strip() == "***":
        return {"valid": False, "message": "Token is required", "scopes": [], "warnings": []}

    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        rate_r = requests.get("https://api.github.com/rate_limit", headers=headers, timeout=15)
        if not rate_r.ok:
            return {
                "valid": False,
                "message": rate_r.json().get("message", f"HTTP {rate_r.status_code}"),
                "scopes": [],
                "warnings": [],
            }

        scopes_raw = rate_r.headers.get("X-OAuth-Scopes", "")
        scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
        core = rate_r.json().get("resources", {}).get("core", {})
        remaining = int(core.get("remaining", 0))
        reset_at = int(core.get("reset", 0))

        user_r = requests.get("https://api.github.com/user", headers=headers, timeout=15)
        login = user_r.json().get("login", "") if user_r.ok else ""

        warnings: list[str] = []
        missing = RECOMMENDED_GH_SCOPES - set(scopes)
        if missing:
            warnings.append(f"Missing recommended scopes: {', '.join(sorted(missing))}")

        org_accessible = None
        if gh_org:
            org_r = requests.get(f"https://api.github.com/orgs/{gh_org}", headers=headers, timeout=15)
            org_accessible = org_r.ok
            if not org_r.ok:
                warnings.append(f"Cannot access organization '{gh_org}' with this token")

        return {
            "valid": True,
            "message": f"Authenticated as {login}" if login else "Token valid",
            "login": login,
            "scopes": scopes,
            "remaining": remaining,
            "reset_at": reset_at,
            "org_accessible": org_accessible,
            "warnings": warnings,
        }
    except requests.RequestException as exc:
        return {"valid": False, "message": str(exc), "scopes": [], "warnings": []}


def validate_ado_pat(ado_org_url: str, ado_pat: str) -> dict[str, Any]:
    """Validate Azure DevOps org URL + PAT."""
    if not ado_org_url:
        return {"valid": False, "message": "ADO org URL is required", "ado_projects": 0, "ado_repos": 0, "warnings": []}
    if not ado_pat or ado_pat.strip() == "***":
        return {"valid": False, "message": "ADO PAT is required", "ado_projects": 0, "ado_repos": 0, "warnings": []}

    from ado2gh.clients.ado_client import ADOClient

    try:
        client = ADOClient(ado_org_url.rstrip("/"), ado_pat.strip())
        projects = client.list_projects()
        total_repos = 0
        repo_errors: list[str] = []
        for proj in projects[:15]:
            name = proj.get("name", "")
            if not name:
                continue
            try:
                total_repos += len(client.list_repos(name))
            except Exception as exc:
                repo_errors.append(f"{name}: {exc}")

        warnings: list[str] = []
        if projects and total_repos == 0:
            warnings.append(
                "Connected to ADO but no Git repositories were found across visible projects. "
                "Confirm projects contain Git repos and the PAT has Code (read) scope."
            )
        for err in repo_errors[:3]:
            warnings.append(f"Could not list repos for {err}")

        return {
            "valid": True,
            "message": f"Connected — {len(projects)} ADO project(s), {total_repos} Git repo(s)",
            "ado_projects": len(projects),
            "ado_repos": total_repos,
            "warnings": warnings,
        }
    except Exception as exc:
        return {"valid": False, "message": str(exc), "ado_projects": 0, "ado_repos": 0, "warnings": []}
