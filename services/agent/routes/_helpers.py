"""Shared utilities for agent service route handlers.

This module contains helpers migrated from services/agent/routes/_shared.py
that are needed by route handlers but not specific to the LangGraph agent logic.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

import httpx
from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ado2gh.agents.migration_agent.constants import (
    MAX_CHAT_MESSAGE_CHARS,
    MAX_FORM_SUBMISSION_CHARS,
    SESSION_IDLE_TTL_SECONDS,
)
from ado2gh.agents.migration_agent.policies import (
    execution_policy_summary,
    is_admin_request,
    live_execution_block_message,
    request_username,
    session_requires_live_approval,
)
from ado2gh.agents.migration_agent.session.state import normalize_session_status
from ado2gh.agents.migration_agent.utils import IdeAuditBridge, _append_event, mask_secrets
from ado2gh.api.live_approval_scopes import AGENT_SESSION_SCOPE_TYPE
from ado2gh.api.pipeline_runner import MIGRATE_UI_PIPELINE_STEPS
from ado2gh.api.proxy_prefixes import ADO_PROXY_PREFIX, PLATFORM_APPROVALS_PATH
from ado2gh.audit.events import AuditEvent
from ado2gh.auth.service import SESSION_COOKIE, auth_enabled, permissions_for
from services.agent.profiles import DEFAULT_ACCELERATOR_URL, LocalAgentProfile, get_profile
from services.agent.routes._session_registry import (
    _NON_RECONSTRUCTIBLE_KEYS,
    _evict_stale_state,
    _forget_session,
    _is_reconstructible,
    _remember_run,
    _remember_session,
    _runs,
    _session_activity_epoch,
    _sessions,
)

# The in-memory session/run caches and their eviction policy moved to
# _session_registry (docs/STRUCTURAL_CHANGELOG.md); every other route module
# still imports them from here, so they are re-exported rather than left as
# unused imports.
__all__ = [
    "SESSION_IDLE_TTL_SECONDS",
    "_NON_RECONSTRUCTIBLE_KEYS",
    "_evict_stale_state",
    "_forget_session",
    "_is_reconstructible",
    "_remember_run",
    "_remember_session",
    "_runs",
    "_session_activity_epoch",
    "_sessions",
]

AGENT_DEFAULT_PIPELINE_STEPS = [s["id"] for s in MIGRATE_UI_PIPELINE_STEPS]
MIGRATE_STEP_IDS = frozenset({
    "migrate", "migrate_repos", "convert_pipelines", "map_secrets", "convert_metadata",
})

try:
    _ACTIVE_PROFILE = get_profile(os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight"))
except KeyError:
    _ACTIVE_PROFILE = None

# services.agent.profiles.LocalAgentProfile already reads ACCELERATOR_URL (and
# applies the same environment override), so it is the one reader of that
# variable; this module used to parse it a second time with a different
# fallback ("http://accelerator:8080" vs. the profile's own default).
ACCEL_URL = _ACTIVE_PROFILE.accelerator_url if _ACTIVE_PROFILE else DEFAULT_ACCELERATOR_URL
_ACCEL_REQUEST_TIMEOUT_SECONDS = (
    _ACTIVE_PROFILE.accelerator_request_timeout_seconds if _ACTIVE_PROFILE else 120.0
)
_ACCEL_HEALTH_TIMEOUT_SECONDS = (
    _ACTIVE_PROFILE.accelerator_health_timeout_seconds if _ACTIVE_PROFILE else 3.0
)
_ACCEL_GET_TIMEOUT_SECONDS = (
    _ACTIVE_PROFILE.accelerator_get_timeout_seconds if _ACTIVE_PROFILE else 60.0
)

_audit = IdeAuditBridge()

SSE_EVENT_LIMIT_REPLY = (
    "Stream ended: this run produced more events than one response may carry. "
    "Send another message to continue."
)


def client_error_detail(exc: BaseException) -> str:
    """Render an exception for an HTTP client: type name plus a masked message.

    Route handlers used to interpolate ``str(exc)`` straight into a response body and server-sent event error
    frames, handing the caller whatever the failing library put in the message — internal hosts, paths, and any
    secret value embedded in it. Nothing sent back to a caller may contain a secret value, and an error must not
    describe internal topology to someone who was never authenticated (register items THR-02-004, CA-003).

    Args:
        exc: The exception being reported to the caller.

    Returns:
        ``"<ClassName>: <masked message>"``, safe to put on the wire.
    """
    return f"{type(exc).__name__}: {mask_secrets(str(exc))}"


class RunStatus(str, Enum):
    """Lifecycle states reported for an agent run."""

    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanPhaseBody(BaseModel):
    """Request body selecting which migration phase to plan."""

    phase: Optional[str] = Field(default=None, max_length=200)


class SessionRequest(BaseModel):
    """Request body for creating an agent session.

    ``dry_run`` is a three-state field: ``True``/``False`` decide, and an omitted field defers to the deployment
    profile's ``dry_run_default``. A preview run is the default and a real run needs an explicit opt-in, so that
    profile default must stay reachable (register item GAP-079). The field used to be declared ``bool = True``,
    which made the fallback unreachable.
    """

    profile_id: str = Field(default="lightweight", max_length=200)
    prompt: str = Field(default="", max_length=MAX_CHAT_MESSAGE_CHARS)
    dry_run: Optional[bool] = None
    model_id: Optional[str] = Field(default=None, max_length=200)
    execute_pev: bool = False


class SessionMessageRequest(BaseModel):
    """Request body carrying one operator message for a session turn."""

    message: str = Field(default="", max_length=MAX_CHAT_MESSAGE_CHARS)


class FormSubmitRequest(BaseModel):
    """Request body with the operator's answers to a form the agent asked them to fill in."""

    values: dict[str, Any] = Field(default_factory=dict)
    form_instance_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "Id of the exact form this submission answers. When given, it must match the "
            "pending form or the submission is refused as stale. Omitted (older console): accepted."
        ),
    )

    @field_validator("values")
    @classmethod
    def _within_size_limit(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Reject a submission larger than the documented boundary limit.

        Form answers are free-form and land in the model's context, so a body with no size limit lets a caller
        make the server consume unbounded memory and processing time. Raising here makes FastAPI answer 422.
        Every request body that reaches the model must carry a maximum size (register item THR-10-002).

        Args:
            values: The submitted answers, keyed by form field name.

        Returns:
            The same answers, unchanged, when they fit the limit.

        Raises:
            ValueError: When the serialised body exceeds ``MAX_FORM_SUBMISSION_CHARS``.
        """
        if len(json.dumps(values, default=str)) > MAX_FORM_SUBMISSION_CHARS:
            raise ValueError(
                f"form submission exceeds {MAX_FORM_SUBMISSION_CHARS} characters",
            )
        return values


class ExecutionModeRequest(BaseModel):
    """Request body switching a session between dry-run and live execution."""

    dry_run: bool


class ProvisionRequest(BaseModel):
    """Request body for provisioning a session's access tier.

    Carries no ``actor``: the identity that decides whether the ``write`` tier may be granted is the
    authenticated platform user resolved from the request, never a free-text field the caller writes into the
    body — a body field would let any caller claim to be an approver (register item GAP-019).
    """

    tier: Literal["read", "write"] = "read"
    reason: str = Field(default="", max_length=MAX_CHAT_MESSAGE_CHARS)


class RemediateRequest(BaseModel):
    """Request body asking the agent to retry one repository.

    Carries no ``retry_count``: the escalation counter is held server-side, on the session itself, so a client
    cannot reset its own count and retry past the ceiling forever (register item THR-10-003).
    """

    repo_key: str = Field(default="", max_length=400)


class ApprovalRequest(BaseModel):
    """Request body recording an operator's approval decision."""

    approved: bool
    reason: str = Field(default="", max_length=MAX_CHAT_MESSAGE_CHARS)


_PLANNER_SYSTEM = (
    "You are the migration planner subagent. Summarize the structured plan for an operator: "
    "target phase, repo count, execution order, dry-run vs live, and prerequisites."
)


def _profile(profile_id: Optional[str] = None) -> LocalAgentProfile | None:
    """Load a deployment profile by id, defaulting to the configured local one.

    Returns:
        The requested profile, or the process-wide active profile when the id is
        unknown. None only when no active profile could be loaded at import.
    """
    pid = profile_id or os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight")
    try:
        return get_profile(pid)
    except KeyError:
        return _ACTIVE_PROFILE


def _session_token_from_request(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.cookies.get(SESSION_COOKIE) or None


def _session_accel_token(session_id: str | None) -> str | None:
    if session_id and session_id in _sessions:
        token = _sessions[session_id].get("session_token")
        if token:
            return str(token)
    return None


def _accel_headers(session_token: str | None = None) -> dict[str, str]:
    if session_token:
        return {"Cookie": f"{SESSION_COOKIE}={session_token}"}
    env_cookie = os.environ.get("ADO2GH_SESSION_COOKIE", "").strip()
    if env_cookie:
        if "=" in env_cookie:
            return {"Cookie": env_cookie}
        return {"Cookie": f"{SESSION_COOKIE}={env_cookie}"}
    return {}


async def _accel_post_impl(path: str, body: dict, *, session_token: str | None = None) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=_ACCEL_REQUEST_TIMEOUT_SECONDS, headers=_accel_headers(session_token),
    ) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _accel_get_impl(path: str, *, session_token: str | None = None) -> dict:
    async with httpx.AsyncClient(
        base_url=ACCEL_URL, timeout=_ACCEL_GET_TIMEOUT_SECONDS, headers=_accel_headers(session_token),
    ) as client:
        r = await client.get(path)
        r.raise_for_status()
        return r.json()




async def _accel_post(path: str, body: dict, *, session_token: str | None = None) -> dict:
    """Lazy wrapper — picks up patches on services.agent.main."""
    import sys
    _m = sys.modules.get("services.agent.main")
    if _m and hasattr(_m, "_accel_post"):
        return await _m._accel_post(path, body, session_token=session_token)
    return await _accel_post_impl(path, body, session_token=session_token)


async def _accel_get(path: str, *, session_token: str | None = None) -> dict:
    """Lazy wrapper — picks up patches on services.agent.main."""
    import sys
    _m = sys.modules.get("services.agent.main")
    if _m and hasattr(_m, "_accel_get"):
        return await _m._accel_get(path, session_token=session_token)
    return await _accel_get_impl(path, session_token=session_token)


def _platform_user(request: Request | None = None) -> str | None:
    """Resolve the authenticated username for a request.

    Returns:
        The username from the request's platform session, or None when there is
        no request or no authenticated user.
    """
    if request is None:
        return None
    return request_username(request)


def _audit_actor(request: Request | None = None) -> tuple[str, str | None]:
    if request is None:
        return "system", None
    actor = _platform_user(request)
    role = None
    if auth_enabled():
        permissions = permissions_for(request)
        if permissions:
            role = permissions.get("role")
    return actor, role


def _require_operate(request: Request) -> None:
    """Require operate permission."""
    if auth_enabled():
        from ado2gh.auth.service import permissions_for
        user = getattr(request.state, "platform_user", None) if request else None
        perms = permissions_for(user.role) if user else {}
        if not perms.get("can_operate", False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operate_permission_required")


def _require_approve_live(request: Request) -> None:
    """Require approve_live permission.

    An identity gate only, and inert while ``ADO2GH_AUTH_ENABLED`` is unset. It is not the decision to allow a
    real, non-preview run: that decision belongs to ``policies.enforce_live_mode_request`` alone, and every
    entry point that writes ``dry_run=False`` must route through it rather than deciding on its own (register
    item THR-06-004).
    """
    if auth_enabled():
        from ado2gh.auth.service import permissions_for
        user = getattr(request.state, "platform_user", None) if request else None
        perms = permissions_for(user.role) if user else {}
        if not perms.get("can_approve_live_execution", False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="approve_live_permission_required")


def _resolve_model_id(requested: str | None) -> tuple[str | None, bool, bool]:
    """Resolve model ID and return (model_id, degraded, unconfigured)."""
    from ado2gh.agents.migration_agent.runtime.llm_bridge import _get_model_config, resolve_langchain_llm

    if not requested:
        requested = os.environ.get("ADO2GH_DEFAULT_LLM_MODEL")

    cfg = _get_model_config(requested)
    resolved_id = cfg.id if cfg else requested
    llm, degraded, unconfigured, _ = resolve_langchain_llm(requested)
    if unconfigured:
        degraded = True
    return resolved_id, degraded, unconfigured


def _get_accessible_session(
    session_id: str,
    request: Request | None = None,
) -> dict[str, Any]:
    """Get session if accessible to current user."""
    if session_id not in _sessions:
        hydrated = _try_hydrate_session(session_id)
        if hydrated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        _remember_session(session_id, hydrated)

    if auth_enabled() and request:
        viewer = request_username(request)
        admin = is_admin_request(request)
        owner = _sessions[session_id].get("user_username")
        if viewer and not admin and owner and owner != viewer:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return _sessions[session_id]


def _try_hydrate_session(session_id: str) -> dict[str, Any] | None:
    """Load a persisted HTTP session into memory without cross-session state."""
    try:
        from ado2gh.agents.migration_agent.graph import clear_langgraph_thread_sync
        from ado2gh.agents.migration_agent.session.lifecycle import (
            apply_model_selection,
            new_isolated_agent_session,
        )
        from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

        record = MigrationSessionStore().get_session(session_id)
        if not record:
            return None
        # Unlike a fresh session_id, this one may already have LangGraph
        # thread state from before the process restarted; clear it so the
        # rehydrated session carries no plan-execute-validate loop (PEV) run state (matches this
        # function's "without cross-session state" contract).
        clear_langgraph_thread_sync(session_id)
        session = new_isolated_agent_session(
            session_id,
            profile_id=str(record.get("profile_id") or "lightweight"),
            dry_run=bool(record.get("dry_run", True)),
            user_username=record.get("user_username"),
        )
        apply_model_selection(
            session,
            selected_model_id=str(record.get("model_id") or "") or None,
        )
        session["messages"] = list(record.get("messages") or [])
        session["migration_plan"] = record.get("migration_plan")
        session["pending_form"] = record.get("pending_form")
        session["status"] = str(record.get("status") or "idle")
        session["created_at"] = record.get("created_at")
        session["updated_at"] = record.get("last_activity_at") or record.get("created_at")
        session["iteration_count"] = int(record.get("iteration_count", 0) or 0)
        session["pev_retry_count"] = int(record.get("pev_retry_count", 0) or 0)
        return session
    except Exception:
        return None


_THINKING_EVENT_KINDS = frozenset({
    "thinking", "status", "progress", "tool_call", "tool_result", "task_update",
})


def _thinking_log_for_current_turn(all_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return thinking/status events for the active user turn only."""
    last_user_idx = -1
    for i, message in enumerate(all_messages):
        if message.get("kind") == "message" and message.get("role") == "user":
            last_user_idx = i
    turn_messages = all_messages[last_user_idx + 1 :] if last_user_idx >= 0 else all_messages
    return [
        {
            "kind": message.get("kind"),
            "content": message.get("content", ""),
            "subagent": message.get("subagent") or "orchestrator",
            "meta": message.get("meta"),
            "timestamp": message.get("timestamp"),
        }
        for message in turn_messages
        if message.get("kind") in _THINKING_EVENT_KINDS
    ]


def _prune_stale_thinking_events(session: dict[str, Any]) -> None:
    """Remove thinking/status events from prior user turns in session storage."""
    messages = session.get("messages") or []
    last_user_idx = -1
    for i, message in enumerate(messages):
        if message.get("kind") == "message" and message.get("role") == "user":
            last_user_idx = i
    if last_user_idx < 0:
        return
    pruned: list[dict[str, Any]] = []
    for i, message in enumerate(messages):
        kind = message.get("kind", "message")
        if kind == "message":
            pruned.append(message)
        elif kind in _THINKING_EVENT_KINDS and i > last_user_idx:
            pruned.append(message)
    session["messages"] = pruned


def _session_payload(session_id: str) -> dict[str, Any]:
    """Build session response payload."""
    session = _sessions.get(session_id, {})
    # Only include message events in the chat log — thinking/status events are streamed live via a server-sent
    # event stream but should not appear as chat messages.
    # The plan-execute-validate loop (PEV) agents (planner/executor/validator) only emit thinking events.
    all_messages = session.get("messages", [])
    chat_messages = [m for m in all_messages if m.get("kind") == "message"]
    thinking_log = _thinking_log_for_current_turn(all_messages)
    raw_status = session.get("status", "idle")
    return {
        "session_id": session_id,
        "profile_id": session.get("profile_id"),
        "status": normalize_session_status(raw_status),
        "live_approval_status": session.get("live_approval_status"),
        "dry_run": session.get("dry_run", True),
        "selected_model_id": session.get("selected_model_id"),
        "llm_degraded": session.get("llm_degraded", False),
        "llm_unconfigured": session.get("llm_unconfigured", False),
        "llm_available": session.get("llm_available", True),
        "messages": chat_messages,
        "thinking_log": thinking_log,
        "migration_plan": session.get("migration_plan"),
        "run_id": session.get("run_id"),
        "pipeline_run_id": session.get("pipeline_run_id") or session.get("run_id"),
        "discovery_snapshot": session.get("discovery_snapshot"),
        "discovery_fetched_at": session.get("discovery_fetched_at"),
        "pending_form": session.get("pending_form"),
        "tasks": session.get("tasks", []),
        "execution_policy": execution_policy_summary(session),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
    }


def _add_message(
    session_id: str,
    role: str,
    content: str,
    *,
    kind: str = "message",
    subagent: str | None = None,
) -> None:
    """Append a message to a session through the one writer that masks secrets.

    Secret values must never appear in anything the server stores or sends out — messages, logs, audit rows,
    stored state, or the session transcript itself (register item CA-003). This function used to build and
    append the message entry itself, which made it a second, unmasked writer alongside ``utils._append_event``
    — the function that documents itself as the sole place where that masking happens. Every writer of the
    session transcript must go through that one place rather than building its own entry and appending it
    directly (register items THR-02-002, GAP-011 residual). This function now delegates to it, so every route
    that appends a message gets the same masking, redaction and ``updated_at`` bookkeeping.

    Args:
        session_id: Session to append to; an unknown id is a no-op.
        role: Who is speaking — ``assistant``, ``system`` or ``user``.
        content: The message body, masked by the writer before it is stored.
        kind: The event kind the console dispatches on.
        subagent: Which agent produced the entry, when it matters to the console.
    """
    session = _sessions.get(session_id)
    if session is None:
        return
    _append_event(session, role=role, content=content, kind=kind, subagent=subagent)


async def _assert_deployment_profile_active(profile_id: str) -> None:
    """Assert that the deployment profile is active.

    Raises:
        HTTPException: 404 when no profile matches the id.
    """
    profile = _profile(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    # Additional profile validation can be added here


async def _build_migration_plan(
    session: dict[str, Any],
    session_token: str | None,
    *,
    phase: str | None = None,
    repository_id: str | None = None,
) -> dict[str, Any]:
    """Build migration plan from discovery data in the session.

    If a specific repository_id is provided, build a single-repo plan.
    Otherwise, build a plan from all repos in the discovery snapshot.
    """
    from datetime import datetime, timezone

    dry_run = session.get("dry_run", True)
    discovery = session.get("discovery_snapshot") or {}
    repos_data = discovery.get("repos", []) if isinstance(discovery, dict) else []

    # Filter to the requested repository if specified
    if repository_id:
        repos_data = [
            r for r in repos_data
            if (r.get("id") == repository_id
                or r.get("name") == repository_id
                or r.get("repo_name") == repository_id
                or f"{r.get('project', '')}/{r.get('name', '')}" == repository_id
                or f"{r.get('project', '')}/{r.get('repo_name', '')}" == repository_id)
        ]
        if not repos_data:
            # No matching repository in discovery — validate against ADO API
            # Parse repository_id as "project/repo" or just "repo"
            if "/" in repository_id:
                project, repo_name = repository_id.split("/", 1)
            else:
                project = ""
                repo_name = repository_id

            # Try to validate via accelerator API
            try:
                if _accel_get:
                    if project:
                        result = await _accel_get(
                            f"{ADO_PROXY_PREFIX}/projects/{project}/repos/{repo_name}",
                            session_token=session_token,
                        )
                    else:
                        # If no project specified, we can't validate - return error
                        return {
                            "error": "repo_not_found",
                            "message": f"Repository '{repository_id}' not found in discovery data. Please specify as Project/RepoName format.",
                            "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                        }
                    if result and result.get("error"):
                        return {
                            "error": "repo_not_found",
                            "message": f"Repository '{repository_id}' not found in Azure DevOps.",
                            "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                        }
                    # Repository exists in ADO, add to plan
                    repos_data = [{"id": repository_id, "name": repository_id, "scopes": ["git", "pipelines"], "project": project}]
                else:
                    return {
                        "error": "repo_not_found",
                        "message": f"Repository '{repository_id}' not found in discovery data and accelerator unavailable for validation.",
                        "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                    }
            except Exception:
                return {
                    "error": "repo_not_found",
                    "message": f"Repository '{repository_id}' not found in discovery data.",
                    "available_repos": [f"{r.get('project', '')}/{r.get('repo_name', r.get('name', ''))}" for r in discovery.get("repos", [])],
                }

    # Build work items aligned with migrate-tab pipeline scopes
    from ado2gh.agents.migration_agent.nodes.executor.plan import (
        finalize_agent_migration_plan,
        repo_config_from_discovery,
    )
    from ado2gh.agents.migration_agent.nodes.executor.scope import build_agent_work_items_for_session

    repo_configs = [
        repo_config_from_discovery(r, session) for r in repos_data if isinstance(r, dict)
    ]
    work_items = build_agent_work_items_for_session(
        repo_configs,
        session,
        db=None,
    ) if repo_configs else []

    plan = {
        "repos": [{"id": r.get("id", r.get("repo_name", r.get("name", ""))), "name": r.get("repo_name", r.get("name", ""))} for r in repos_data],
        "work_items": work_items,
        "dry_run": dry_run,
        "assumptions": [
            "Plan built from discovery snapshot",
            "Pipeline follows Migrate tab: connect → analyze_deps → migrate_repos → convert_pipelines → validate",
        ],
        "blocked_items": [r for r in repos_data if r.get("blocked")],
        "revision": 1,
        "repo_count": len(repos_data),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    resolved_phase = (phase or session.get("plan_phase") or "").strip() or None
    if resolved_phase:
        plan["phase"] = resolved_phase
    if repository_id:
        session = {**session, "plan_repository_id": repository_id}
    return finalize_agent_migration_plan(plan, session)




async def _enqueue_session_live_approval(
    session: dict[str, Any], *, session_token: str | None = None,
) -> None:
    """Open or reuse the platform approval row for this session's live request.

    Before this, a session's live request only flipped ``live_approval_status``
    to ``pending`` in memory: no row was ever created, so the request carried no
    id an approver could act on and no audit trail recorded it (GAP-110).
    Routing the request through the same ``POST /v1/platform/approvals``
    endpoint the migrate and pipeline routes already use gives the session an
    approval id — kept on ``live_approval_id`` so ``approve_session`` can
    forward the operator's decision to it, the same way it already does when
    the id is present — and a ``platform.live_execution.requested`` audit
    event on the accelerator side. ``live_approval_status`` becomes a cache of
    the row's own status rather than a value this function invents.

    Args:
        session: The agent session requesting live execution.
        session_token: Cookie value identifying the requester to the
            accelerator, normally read from the live request. Falls back to
            the session's own stored token so a caller with no live request
            in hand — the session-creation path — can still make the call.
    """
    from ado2gh.agents.migration_agent.session.state import set_session_idle

    token = session_token or session.get("session_token")
    try:
        row = await _accel_post(
            PLATFORM_APPROVALS_PATH,
            {
                "scope_type": AGENT_SESSION_SCOPE_TYPE,
                "scope_id": session["session_id"],
                "profile_id": session.get("profile_id"),
                "reason_request": "Agent session requested live execution",
            },
            session_token=token,
        )
    except httpx.HTTPError as exc:
        session["live_approval_status"] = "pending"
        _audit.record(
            AuditEvent.SESSION_REQUEST_LIVE_FAILED.value,
            profile_id=session.get("profile_id"),
            session_id=session["session_id"],
            metadata={"error": str(exc)},
        )
    else:
        session["live_approval_id"] = row.get("id")
        session["live_approval_status"] = row.get("status", "pending")
        _audit.record(
            AuditEvent.SESSION_REQUEST_LIVE.value,
            profile_id=session.get("profile_id"),
            session_id=session["session_id"],
            metadata={"approval_id": row.get("id")},
        )
    set_session_idle(session)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()


async def _check_accelerator() -> tuple[bool, str | None]:
    """Check if accelerator service is reachable (short timeout — must not block agent /health)."""
    import httpx

    try:
        async with httpx.AsyncClient(base_url=ACCEL_URL, timeout=_ACCEL_HEALTH_TIMEOUT_SECONDS) as client:
            r = await client.get("/health")
            r.raise_for_status()
        return True, None
    except Exception as exc:
        return False, str(exc)


# Legacy plan-execute-validate loop (PEV) stubs for compatibility during migration
# These will be removed once all route handlers are fully migrated to orchestrator
async def _try_start_pev_run(session_id: str, request: Request | None = None) -> bool:
    """Start the plan-execute-validate loop (PEV) when allowed. Returns False if live execution is blocked pending approval."""
    from ado2gh.agents.migration_agent.runtime.orchestrator import process_user_message

    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if session_requires_live_approval(session):
        msg = live_execution_block_message(session)
        if request:
            await _enqueue_session_live_approval(session)
        _add_message(session_id, "assistant", msg, kind="message")
        return False

    # Use orchestrator to start the plan-execute-validate loop
    session_token = _session_accel_token(session_id)
    result = await process_user_message(
        session,
        "Start migration execution",
        deps={
            "accel_get": _accel_get,
            "accel_post": _accel_post,
            "build_plan": _build_migration_plan,
            "session_token": session_token,
        },
    )

    if result.reply:
        _add_message(session_id, "assistant", result.reply, kind="message")

    return True
