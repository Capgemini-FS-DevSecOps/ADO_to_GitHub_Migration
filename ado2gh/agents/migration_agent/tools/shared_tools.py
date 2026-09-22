"""Shared tools available to every migration agent role.

Also the one place a model-chosen endpoint is joined onto a fixed API prefix
(:func:`join_api_path`) and the one place a caught exception becomes a tool
result (:func:`tool_error`).
"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Callable
from urllib.parse import unquote

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from ado2gh.agents.migration_agent.utils import IdeAuditBridge
from ado2gh.audit.redaction import redact_text

_MAX_ENDPOINT_CHARS = 2048
_MAX_ERROR_DETAIL_CHARS = 500
# Refused-path audit records keep only this many characters of the endpoint,
# applied after masking so the cut can never split a secret shape in two.
_MAX_AUDITED_ENDPOINT_CHARS = 200
# redact_text catches named secret shapes; a URL can carry a credential in forms
# it does not name — basic-auth userinfo, or an opaque `sig=`/`code=` query
# value. Both are dropped wholesale from an error detail (THR-02-003).
_URL_USERINFO_RE = re.compile(r"(?<=://)[^/\s@]+(?=@)")
_URL_QUERY_RE = re.compile(r"(?<=\?)[^\s\"'<>]+")

_audit = IdeAuditBridge()


class ApiPathError(ValueError):
    """A model-chosen endpoint did not stay inside its fixed API prefix."""


def _refuse_path(prefix: str, endpoint: str, reason: str) -> ApiPathError:
    """Record a refused tool path and build the error to raise (CA-004).

    A refusal that only ever shows up as a tool result is visible to the model
    and to nobody else. The durable record goes through the same masking audit
    bridge the agent's routes use, and a failure to write it never blocks the
    refusal itself. The endpoint is masked before it is truncated, using the
    same secret-shape, userinfo and query masking :func:`tool_error` applies:
    truncating first can cut a fixed-length secret shape in two, leaving a
    fragment too short to be recognised and masked (CA-003).

    Args:
        prefix: The prefix the endpoint failed to stay under.
        endpoint: The model-supplied endpoint, masked and truncated before it
            is stored.
        reason: Machine-readable refusal reason.

    Returns:
        The :class:`ApiPathError` the caller should raise.
    """
    try:
        masked_endpoint = _URL_QUERY_RE.sub("***", _URL_USERINFO_RE.sub("***", redact_text(endpoint)))
        _audit.record(
            "agent.tool.path_refused",
            detail=reason,
            metadata={"prefix": prefix, "endpoint": masked_endpoint[:_MAX_AUDITED_ENDPOINT_CHARS]},
        )
    except Exception:  # noqa: S110 - auditing must never break the refusal it records
        pass
    return ApiPathError(reason)


def join_api_path(prefix: str, endpoint: str) -> str:
    """Join a model-chosen endpoint onto a fixed API prefix without letting it escape.

    Three stack behaviours have to be modelled together. httpx resolves
    dot-segments when it merges a path onto ``base_url`` (measured on httpx
    0.28.1: ``/v1/ado/../../v1/migrate/git-mirror`` reaches
    ``/v1/migrate/git-mirror``). uvicorn percent-decodes the path before it
    routes, so ``..%2f`` is traversal too. And httpx drops everything from the
    first ``#`` before it builds the request path, so ``..#`` reads as one
    ordinary segment to any check that normalises the whole string while the
    wire sees a bare ``..`` — measured, ``/v1/ado/..#`` is sent as ``/v1``.
    A ``#`` in an API path is never legitimate here, so it is refused outright
    rather than modelled; with it gone the probe below and the transport path
    agree.

    What is returned is the unmodified join, so a legitimate endpoint still
    reaches the accelerator byte for byte as it did before.

    Args:
        prefix: The prefix the request must stay under, e.g. ``/v1/ado``.
        endpoint: The model-supplied endpoint, with or without a leading slash.
            Any query string is carried through untouched.

    Returns:
        The joined request path, verified to still resolve under ``prefix``.

    Raises:
        ApiPathError: When the endpoint is over-long, carries a fragment, or
            normalises to a path outside ``prefix``.
    """
    if len(endpoint) > _MAX_ENDPOINT_CHARS:
        raise _refuse_path(prefix, endpoint, "endpoint_too_long")
    trimmed = prefix.strip("/")
    base = f"/{trimmed}/" if trimmed else "/"
    candidate = base + endpoint.lstrip("/")
    decoded = unquote(candidate.split("?", 1)[0]).replace("\\", "/")
    if "#" in candidate or "#" in decoded:
        raise _refuse_path(prefix, endpoint, "endpoint_carries_fragment")
    probe = posixpath.normpath(decoded)
    if not f"{probe}/".startswith(base):
        raise _refuse_path(prefix, endpoint, f"endpoint_escapes_prefix:{base}")
    return candidate


def tool_error(exc: Exception, **extra: object) -> dict[str, Any]:
    """Describe a failed tool call without handing the raw exception to the model.

    httpx exception strings carry the full request URL, and a tool result is both
    persisted to the LangGraph checkpoint and replayed into the prompt, so the
    raw text is not a safe payload.

    Args:
        exc: The caught exception.
        **extra: Extra keys merged into the result, e.g. ``project``.

    Returns:
        ``error`` (the exception class name) and ``detail`` (the message with
        secret shapes, URL userinfo and URL query strings masked, then capped),
        plus ``status_code`` when the exception carries an HTTP response.
    """
    detail = _URL_QUERY_RE.sub("***", _URL_USERINFO_RE.sub("***", redact_text(str(exc))))
    out: dict[str, Any] = {
        "error": type(exc).__name__,
        "detail": detail[:_MAX_ERROR_DETAIL_CHARS],
    }
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        out["status_code"] = status
    out.update(extra)
    return out


class GetCurrentProfileArgs(BaseModel):
    """No arguments — returns the session user's migration environment."""


async def fetch_current_profile(
    accel_get: Callable[..., Any] | None = None,
    *,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve the active migration profile and API access paths for the current session.

    Returns:
        The deployment and migration profile ids, the resolved profile, the
        session's dry-run flag and the ADO/GitHub proxy paths. When the
        accelerator is unreachable the dict carries ``error`` and the
        deployment profile id only.
    """
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
        return tool_error(exc, deployment_profile_id=deployment_profile_id)

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
    accel_get: Callable[..., Any] | None = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> StructuredTool:
    """Build the get_current_profile StructuredTool for LangChain bind_tools.

    Returns:
        A no-argument StructuredTool that returns the session's migration
        environment.
    """

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
    accel_get: Callable[..., Any] | None = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> list[StructuredTool]:
    """Prepend shared tools to an agent-specific tool list.

    Returns:
        A new list with get_current_profile first, or ``tools`` unchanged when
        a tool of that name is already present.
    """
    shared = build_get_current_profile_tool(
        accel_get,
        session_token=session_token,
        session_getter=session_getter,
    )
    names = {t.name for t in tools}
    if shared.name in names:
        return tools
    return [shared, *tools]
