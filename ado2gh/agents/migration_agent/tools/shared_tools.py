"""Shared tools available to every migration agent role."""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


class GetCurrentProfileArgs(BaseModel):
    """No arguments — returns the session user's migration environment."""


async def fetch_current_profile(
    accel_get: Any = None,
    *,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve the active migration profile and API access paths for the current session."""
    session = session_getter() if session_getter else {}
    deployment_profile_id = str(session.get("profile_id") or "lightweight")

    if not accel_get:
        return {
            "error": "accelerator_unavailable",
            "deployment_profile_id": deployment_profile_id,
        }

    try:
        settings = await accel_get("/v1/settings", session_token=session_token)
    except Exception as exc:
        return {
            "error": str(exc),
            "deployment_profile_id": deployment_profile_id,
        }

    if not isinstance(settings, dict):
        return {
            "error": "invalid_settings_response",
            "deployment_profile_id": deployment_profile_id,
        }

    active_id = settings.get("active_profile_id")
    migration_profiles = [
        p for p in (settings.get("migration_profiles") or []) if isinstance(p, dict)
    ]
    profile_ids = {str(p.get("id")) for p in migration_profiles if p.get("id")}

    migration_profile_id = (
        deployment_profile_id
        if deployment_profile_id in profile_ids
        else active_id
    )
    if not migration_profile_id and migration_profiles:
        migration_profile_id = migration_profiles[0].get("id")

    profile_detail = next(
        (p for p in migration_profiles if p.get("id") == migration_profile_id),
        None,
    )
    if migration_profile_id and not profile_detail:
        try:
            fetched = await accel_get(
                f"/v1/settings/profiles/{migration_profile_id}",
                session_token=session_token,
            )
            if isinstance(fetched, dict) and not fetched.get("error"):
                profile_detail = fetched
        except Exception:
            profile_detail = None

    ado_org_url = (profile_detail or {}).get("ado_org_url") or ""
    gh_org = (profile_detail or {}).get("gh_org") or ""

    return {
        "deployment_profile_id": deployment_profile_id,
        "migration_profile_id": migration_profile_id,
        "active_profile_id": active_id,
        "dry_run": bool(session.get("dry_run", True)),
        "profile": profile_detail,
        "api_access": {
            "ado_org_url": ado_org_url,
            "gh_org": gh_org,
            "ado_proxy": "Use ado_api_query with endpoint paths relative to the ADO org",
            "github_proxy": "Use github_api_query with endpoint paths like repos/{org}/{repo}",
            "discovery": (
                f"/v1/settings/profiles/{migration_profile_id}/discovery"
                if migration_profile_id
                else None
            ),
        },
    }


def build_get_current_profile_tool(
    accel_get: Any = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> StructuredTool:
    """Build the get_current_profile StructuredTool for LangChain bind_tools."""

    async def get_current_profile() -> dict[str, Any]:
        """Return the current user's migration environment and profile for API access."""
        return await fetch_current_profile(
            accel_get,
            session_token=session_token,
            session_getter=session_getter,
        )

    return StructuredTool.from_function(
        coroutine=get_current_profile,
        name="get_current_profile",
        description=(
            "Return the current user's migration environment and profile for API access. "
            "Includes migration_profile_id, ADO org URL, GitHub org, dry_run mode, and "
            "discovery/API proxy paths. Call this before ado_api_query, github_api_query, "
            "or call_accelerator when org/profile context is unknown."
        ),
        args_schema=GetCurrentProfileArgs,
    )


def append_shared_tools(
    tools: list[StructuredTool],
    *,
    accel_get: Any = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> list[StructuredTool]:
    """Prepend shared tools to an agent-specific tool list."""
    shared = build_get_current_profile_tool(
        accel_get,
        session_token=session_token,
        session_getter=session_getter,
    )
    names = {t.name for t in tools}
    if shared.name in names:
        return tools
    return [shared, *tools]
