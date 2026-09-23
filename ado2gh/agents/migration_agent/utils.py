"""Shared utility functions for the migration agent.

Extracted from session_orchestrator.py and orchestration/session_state.py.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from ado2gh.audit import redact_payload

if TYPE_CHECKING:  # AuditWriter is imported lazily so no audit destination opens on import
    from ado2gh.audit import AuditWriter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def coerce_dry_run(value: object, *, default: bool | None = None) -> bool | None:
    """Read a ``dry_run`` field strictly, so only a real boolean counts as a decision.

    ``bool()`` maps ``None`` to ``False`` and ``False`` means *live*, so coercing an
    absent or malformed flag reads a missing decision as a request to write to GitHub
    (GAP-076, CA-001). Plan flags originate in language model JSON, so anything other than
    ``True``/``False`` — ``None``, ``"false"``, ``0`` — is treated as unspecified and
    the caller's safe default applies instead.

    Args:
        value: The raw ``dry_run`` field, from a plan, session or executor result.
        default: What "unspecified" means to this caller; ``None`` lets the caller
            fall through to another source.

    Returns:
        The boolean the field carries, else ``default``.
    """
    return value if isinstance(value, bool) else default


def resolve_dry_run(
    session: dict[str, Any] | None,
    *,
    migration_plan: dict[str, Any] | None = None,
    executor_result: dict[str, Any] | None = None,
) -> bool:
    """Resolve whether the current plan-execute-validate loop (PEV) cycle is dry-run (default True).

    The first source carrying a real boolean wins; a present-but-malformed flag is
    skipped rather than coerced, so it can never turn a dry run into a live one.
    """
    for source in (executor_result, migration_plan, session):
        if isinstance(source, dict):
            decided = coerce_dry_run(source.get("dry_run"))
            if decided is not None:
                return decided
    return True


def _append_event(  # noqa: PLR0913 - exception-register.md: the message record's own fields
    session: dict[str, Any],
    *,
    role: str,
    content: str,
    kind: str = "message",
    subagent: str | None = None,
    meta: dict | None = None,
) -> dict[str, Any]:
    """Append a message event to the session's message list.

    Sole entry point for the in-memory message list, so this is where CA-003
    masking is applied — everything downstream (the server-sent event stream (SSE), the persisted
    ``messages_json`` column, the chat feed) reads the masked entry it returns.

    Args:
        session: The live agent session dict, mutated in place.
        role: Who is speaking — ``assistant``, ``system`` or ``user``.
        content: The message body, masked before it is stored.
        kind: The event kind the console dispatches on.
        subagent: Which agent produced the entry, when it matters to the UI.
        meta: Structured extras for the entry; redacted before it is stored.

    Returns:
        The stored entry, with ``content`` and ``meta`` already masked and a UTC
        ``timestamp`` added — callers stream this, never their own inputs.
    """
    entry: dict[str, Any] = {
        "role": role,
        "content": mask_secrets(content),
        "kind": kind,
        "timestamp": _now(),
    }
    if subagent:
        entry["subagent"] = subagent
    if meta:
        entry["meta"] = redact_payload(meta)
    session.setdefault("messages", []).append(entry)
    session["updated_at"] = _now()
    return entry


def publish_orchestrator_chat(session: dict[str, Any], content: str) -> str:
    """Publish a user-facing orchestrator message (Markdown) to the chat."""
    from ado2gh.agents.migration_agent.message_format import (
        CHAT_CONTENT_FORMAT,
        normalize_chat_markdown,
    )

    markdown = normalize_chat_markdown(content)
    entry = _append_event(
        session,
        role="assistant",
        content=markdown,
        kind="message",
        subagent="orchestrator",
        meta={"content_format": CHAT_CONTENT_FORMAT},
    )
    return str(entry["content"])


def _stream_entry(
    entry: dict[str, Any],
    *,
    kind: str,
    subagent: str | None = None,
) -> None:
    """Emit an already-appended message entry over the live server-sent event stream (SSE).

    Takes the entry :func:`_append_event` returned rather than the raw values, so the
    masked content is what reaches the wire and never the raw arguments (CA-003).
    A missing stream writer is normal outside a graph invocation and is ignored.

    Args:
        entry: The masked entry returned by :func:`_append_event`.
        kind: The event kind the console dispatches on.
        subagent: Which agent produced the entry, when it matters to the UI.
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        # langgraph's stub declares get_stream_writer() -> StreamWriter
        # (non-Optional; a no-op default outside a graph run), so this is
        # always true today. Kept as a guard against a future langgraph
        # version returning None here instead of a no-op writer.
        if writer:  # type: ignore[truthy-function]
            evt: dict[str, Any] = {"kind": kind, "content": entry["content"]}
            if subagent:
                evt["subagent"] = subagent
            if entry.get("meta"):
                evt["meta"] = entry["meta"]
            writer(evt)
    except Exception:
        pass


def _append_and_stream(
    session: dict[str, Any],
    *,
    role: str,
    content: str,
    kind: str = "thinking",
    subagent: str | None = None,
) -> None:
    """Append an event to the session and emit it for the live server-sent event stream (SSE) in one call.

    Call :func:`_append_event` followed by :func:`_stream_entry` directly when the
    event also carries ``meta``.

    Args:
        session: The live agent session dict, mutated in place.
        role: Who is speaking — ``assistant``, ``system`` or ``user``.
        content: The message body; masked by :func:`_append_event` before it is stored
            or streamed.
        kind: The event kind the console dispatches on.
        subagent: Which agent produced the entry, when it matters to the UI.
    """
    _stream_entry(
        _append_event(session, role=role, content=content, kind=kind, subagent=subagent),
        kind=kind,
        subagent=subagent,
    )


TOOL_STATUS_START: dict[str, str] = {
    "run_migration_pev": "Starting migration pipeline…",
    "fetch_migration_status": "Checking migration run status…",
    "ado_api_query": "Querying Azure DevOps…",
    "github_api_query": "Querying GitHub…",
    "github_api": "Calling GitHub API…",
    "call_accelerator": "Calling accelerator API…",
    "invoke_planner": "Invoking Planner agent…",
    "invoke_bulk_planner": "Invoking Planner agent…",
    "request_user_input": "Collecting operator input…",
    "generate_plan": "Generating migration plan…",
    "validate_workflow_conversion": "Validating workflow conversion locally…",
    "validate_workflow_syntax": "Validating workflow syntax locally…",
    "list_ado_pipelines": "Listing ADO pipelines…",
    "list_github_workflows": "Listing GitHub workflows…",
    "fetch_github_workflow": "Fetching workflow YAML…",
}

TOOL_TASK_IDS: dict[str, str] = {
    "run_migration_pev": "execute",
    "ado_api_query": "discovery",
    "github_api_query": "discovery",
    "github_api": "execute",
    "call_accelerator": "execute",
    "invoke_planner": "plan",
    "generate_plan": "plan",
    "validate_workflow_conversion": "validate",
    "validate_workflow_syntax": "validate",
}


def _reset_turn_status_budget(session: dict[str, Any]) -> None:
    """Clear the per-turn status-message counter at the start of a turn.

    Args:
        session: The live agent session dict, mutated in place.
    """
    session["_status_msg_count"] = 0


def _tool_status_done(tool_name: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return result.get("message") or str(result["error"])
    if tool_name == "run_migration_pev":
        mode = "dry-run" if result.get("dry_run", True) else "live"
        return f"Migration pipeline starting ({mode})."
    if tool_name == "fetch_migration_status":
        return "Migration status updated."
    if tool_name in ("invoke_planner", "invoke_bulk_planner"):
        status = str((result.get("result") or {}).get("status") or result.get("status") or "")
        if status == "awaiting_intake":
            return "Planner handoff paused — more operator input is required."
        if status in ("invoking_planner", "invoking_bulk_planner"):
            return "Planner started — building migration plan…"
        return "Planner handoff complete."
    if tool_name == "request_user_input":
        return "Form presented to operator."
    return "Step complete."


def _emit_tool_call(
    session: dict[str, Any],
    tool_name: str,
    *,
    subagent: str = "orchestrator",
    arguments: dict[str, Any] | None = None,
) -> None:
    """Record and stream a tool invocation (shown in thinking panel as tool, not status)."""
    label = TOOL_STATUS_START.get(tool_name, tool_name)
    meta = {"name": tool_name, "arguments": arguments or {}}
    entry = _append_event(
        session,
        role="system",
        content=label,
        kind="tool_call",
        subagent=subagent,
        meta=meta,
    )
    _stream_entry(entry, kind="tool_call", subagent=subagent)


def _emit_tool_result(
    session: dict[str, Any],
    tool_name: str,
    result: dict[str, Any],
    *,
    subagent: str = "orchestrator",
) -> None:
    """Record and stream a tool result summary."""
    summary = _tool_status_done(tool_name, result)
    _append_event(
        session,
        role="system",
        content=summary,
        kind="tool_result",
        subagent=subagent,
        meta={"name": tool_name, **({"error": result["error"]} if result.get("error") else {})},
    )
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        # See the comment on the sibling guard above: always-truthy today per
        # langgraph's own stub and runtime default, kept intentionally.
        if writer:  # type: ignore[truthy-function]
            writer(redact_payload({
                "kind": "tool_result",
                "content": summary,
                "subagent": subagent,
                "meta": {"tool_name": tool_name, **result},
            }))
    except Exception:
        pass


# ─── User message queuing during plan-execute-validate loop (PEV) execution ───

_CANCELLATION_PHRASES = frozenset({
    "cancel", "stop", "abort", "halt", "cancel migration", "stop migration",
    "cancel execution", "stop execution", "cancel pev", "stop pev",
})


def _is_cancellation_request(message: str) -> bool:
    msg = message.lower().strip()
    return msg in _CANCELLATION_PHRASES or any(
        p in msg for p in ("cancel the migration", "stop the migration", "abort the migration")
    )


def _queue_user_message(session: dict[str, Any], message: str) -> None:
    """Queue a user message while the plan-execute-validate loop (PEV) chain is running."""
    session.setdefault("_message_queue", []).append({
        "message": message,
        "timestamp": _now(),
    })


def _drain_message_queue(session: dict[str, Any]) -> list[str]:
    """Return and clear queued messages."""
    queued = session.pop("_message_queue", [])
    return [item["message"] for item in queued]


def _has_queued_messages(session: dict[str, Any]) -> bool:
    return bool(session.get("_message_queue"))


def _parse_llm_json(text: str) -> dict[str, Any]:
    """Parse JSON from language model output, tolerating markdown code fences and alternate formats.

    Args:
        text: Raw model output, which may wrap the JSON in a ```json fence or prose.

    Returns:
        The decoded object, or an empty dict when the text holds no parsable JSON.
    """
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = {}
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except (json.JSONDecodeError, TypeError):
                pass
    if not isinstance(parsed, dict):
        return {}

    # Normalize alternate formats
    if "action" in parsed and "tool_calls" not in parsed:
        action = parsed.pop("action")
        params = parsed.pop("parameters", parsed.pop("arguments", {}))
        parsed["tool_calls"] = [{"name": action, "arguments": params}]
    if "tool_call" in parsed and "tool_calls" not in parsed:
        tc = parsed.pop("tool_call")
        if isinstance(tc, dict):
            parsed["tool_calls"] = [tc]
        elif isinstance(tc, list):
            parsed["tool_calls"] = tc

    return parsed


def _safe_reply(parsed: dict[str, Any], response_text: str) -> str:
    """Extract a human-readable Markdown reply, never returning raw JSON to the user."""
    from ado2gh.agents.migration_agent.message_format import normalize_chat_markdown

    reply = parsed.get("reply") or ""
    if reply and not reply.strip().startswith("{"):
        return normalize_chat_markdown(reply)
    if parsed.get("tool_calls"):
        return normalize_chat_markdown("Processing your request…")
    if response_text and not response_text.strip().startswith("{"):
        return normalize_chat_markdown(response_text)
    return normalize_chat_markdown(
        "I'm here to help with ADO→GitHub migrations. What would you like to do?"
    )


# ─── Secret masking (moved from local/audit_bridge.py) ───────────────


def mask_secrets(text: str) -> str:
    """Mask secret values in text, keeping only the key name.

    Thin delegate to the platform's single masking choke point (FR-025) so the
    agent recognises every shape the audit writer does — GitHub prefixes, bare
    ADO personal access tokens (PATs), `Bearer <token>` and `key=value` pairs.
    """
    if not text:
        return text
    return str(redact_payload(text))


# ─── Audit bridge (moved from local/audit_bridge.py) ─────────────────


class IdeAuditBridge:
    """Audit bridge for IDE sessions and MCP tool calls."""

    def __init__(self) -> None:
        """Create the bridge without opening a database connection."""
        self._writer: AuditWriter | None = None

    def _ensure_writer(self) -> AuditWriter:
        """Open the audit writer on first use.

        Returns:
            The process-wide :class:`~ado2gh.audit.AuditWriter`, created against
            the configured audit destination the first time an event is
            recorded, so the agent honours ``ADO2GH_AUDIT_DESTINATION`` like
            every other writer.
        """
        if self._writer is None:
            from ado2gh.audit import AuditWriter, create_audit_destination

            self._writer = AuditWriter(create_audit_destination())
        return self._writer

    def record(  # noqa: PLR0913 - exception-register.md: the audit event's own columns
        self,
        action: str,
        *,
        profile_id: str = "",
        repo: str = "",
        detail: str = "",
        actor: str = "local-developer",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Write one agent-side audit event, masking every value it carries (CA-003/CA-004).

        ``repo``, ``detail``, ``session_id`` and ``metadata`` become the event payload;
        ``action``, ``profile_id`` and ``actor`` are the event's own columns. Auditing
        must never break the operation it records, so a write failure is swallowed.

        Args:
            action: The event type, for example ``session.confirm_live``.
            profile_id: Deployment profile the action ran under.
            repo: Repository the action touched, when it targets one.
            detail: Free-text description; masked before it is stored.
            actor: Who performed the action; defaults to the local developer.
            session_id: Agent session the action belongs to.
            metadata: Structured extras; every value is masked before it is stored.

        Returns:
            The new audit record's id, or ``None`` when the write failed.
        """
        try:
            writer = self._ensure_writer()
            payload: dict[str, Any] = {}
            if repo:
                payload["repo"] = repo
            if detail:
                payload["detail"] = mask_secrets(detail)
            if session_id:
                payload["session_id"] = session_id
            if metadata:
                payload["metadata"] = {k: mask_secrets(str(v)) for k, v in metadata.items()}
            return writer.write(
                event_type=action,
                profile_id=profile_id,
                actor=actor,
                payload=payload,
            )
        except Exception:
            return None


def normalize_repo_key(repo_id: str) -> str:
    """Canonical Project/Repo id without leading slashes or outer whitespace."""
    text = str(repo_id or "").strip()
    while text.startswith("/"):
        text = text[1:].strip()
    return text


def canonical_repo_id(repo: dict[str, Any]) -> str:
    """Return the canonical Project/RepoName identifier for a discovery or plan repo."""
    if repo.get("id"):
        return normalize_repo_key(str(repo["id"]))
    project = str(repo.get("project", "") or "").strip()
    repo_name = str(repo.get("repo_name") or repo.get("name") or "").strip()
    if project and repo_name:
        return f"{project}/{repo_name}"
    return repo_name


def canonical_plan_repo_key(
    repo_id: str,
    discovery: dict[str, Any] | None = None,
) -> str:
    """Normalize a repository identifier for plan/executor comparison."""
    key = normalize_repo_key(repo_id)
    if not key:
        return ""
    repos = (discovery or {}).get("repos", []) if isinstance(discovery, dict) else []
    if repos:
        match = find_discovery_repo(key, repos)
        if match:
            return canonical_repo_id(match)
    return key


def repo_key_aliases(
    repo_id: str,
    discovery: dict[str, Any] | None = None,
) -> set[str]:
    """Return canonical and short-name aliases for a repository identifier."""
    canonical = canonical_plan_repo_key(repo_id, discovery)
    aliases: set[str] = set()
    if canonical:
        aliases.add(canonical)
        short = canonical.split("/")[-1]
        if short:
            aliases.add(short)
    return aliases


def plan_repo_key_aliases(
    migration_plan: dict[str, Any] | None,
    discovery: dict[str, Any] | None = None,
) -> set[str]:
    """Collect all repo key aliases referenced by a migration plan."""
    aliases: set[str] = set()
    plan = migration_plan or {}
    raw_ids: list[str] = []
    for repo in plan.get("repos") or []:
        if isinstance(repo, dict):
            rid = repo.get("id") or repo.get("name") or repo.get("repository_id")
        else:
            rid = repo
        if rid:
            raw_ids.append(str(rid))
    for field in ("repository_id",):
        rid = plan.get(field)
        if rid:
            raw_ids.append(str(rid))
    for wi in plan.get("work_items") or []:
        if isinstance(wi, dict) and wi.get("repo"):
            raw_ids.append(str(wi["repo"]))
    for rid in raw_ids:
        aliases.update(repo_key_aliases(rid, discovery))
    return {alias for alias in aliases if alias}


def repo_matches_plan_keys(
    executed_repo_id: str,
    plan_aliases: set[str],
    discovery: dict[str, Any] | None = None,
) -> bool:
    """True when executed repo id matches any plan repo alias."""
    if not plan_aliases:
        return False
    executed_aliases = repo_key_aliases(executed_repo_id, discovery)
    return bool(executed_aliases & plan_aliases)


def normalize_discovery_repo(repo: dict[str, Any]) -> dict[str, Any]:
    """Ensure discovery repo dicts expose canonical id/name fields."""
    repo_id = canonical_repo_id(repo)
    normalized = dict(repo)
    normalized["id"] = repo_id
    normalized.setdefault("name", repo.get("repo_name") or repo.get("name") or repo_id.split("/")[-1])
    normalized.setdefault("repo_name", normalized["name"])
    return normalized


def hydrate_session_target_org(
    session: dict[str, Any],
    discovery: dict[str, Any] | None = None,
) -> None:
    """Copy resolved GitHub org onto the session from discovery or profile settings."""
    if str(session.get("gh_org") or session.get("github_org") or "").strip():
        return
    disc = discovery if isinstance(discovery, dict) else session.get("discovery_snapshot")
    if isinstance(disc, dict):
        org = str(disc.get("gh_org") or "").strip()
        if org:
            session["gh_org"] = org
            session["github_org"] = org
            return
    from ado2gh.agents.migration_agent.nodes.executor.plan import resolve_github_org

    org = resolve_github_org(session=session)
    if org:
        session["gh_org"] = org
        session["github_org"] = org


async def load_discovery_snapshot(
    session: dict[str, Any],
    accel_get: Callable[..., Awaitable[Any]] | None,
    session_token: str | None = None,
) -> dict[str, Any]:
    """Load the profile's discovery snapshot into the session when it is missing.

    A cached snapshot with repos is returned as-is. Discovery is best-effort context,
    so a missing accelerator callable or a failed fetch returns whatever is cached
    rather than raising.

    Args:
        session: The live agent session dict; the fetched snapshot and its timestamp
            are written back onto it.
        accel_get: Accelerator GET callable, or ``None`` when none was injected.
        session_token: Bearer token forwarded to the accelerator.

    Returns:
        The discovery snapshot dict, or an empty dict when none could be obtained.
    """
    discovery = session.get("discovery_snapshot")
    if discovery and isinstance(discovery, dict) and discovery.get("repos"):
        return discovery
    if not accel_get:
        return discovery if isinstance(discovery, dict) else {}
    try:
        profile_id = session.get("profile_id", "lightweight")
        discovery = await accel_get(
            f"/v1/settings/profiles/{profile_id}/discovery",
            session_token=session_token,
        )
        if not isinstance(discovery, dict):
            return {}
        # Mask any credential the accelerator's discovery response carries before
        # it becomes session state, matching the fix applied to the planner's own
        # discovery fetch in commit bf1a07c (nodes/planner_research.py).
        discovery = cast("dict[str, Any]", redact_payload(discovery))
        session["discovery_snapshot"] = discovery
        from datetime import datetime, timezone
        session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
        hydrate_session_target_org(session, discovery)
        return discovery
    except Exception:
        return discovery if isinstance(discovery, dict) else {}


def discovery_repo_names(repos: list[Any]) -> set[str]:
    """Collect canonical and short repo names from discovery data."""
    valid: set[str] = set()
    for r in repos:
        if isinstance(r, dict):
            name = r.get("repo_name") or r.get("name") or ""
            project = r.get("project", "")
            if name:
                valid.add(name)
            if project and name:
                valid.add(f"{project}/{name}")
        elif r:
            valid.add(str(r))
    return valid


def find_discovery_repo(repo_id: str, repos: list[Any]) -> dict[str, Any] | None:
    """Return the normalized discovery repo dict matching repo_id, or None."""
    repo_name_part = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    exact_matches: list[dict[str, Any]] = []
    for r in repos:
        if not isinstance(r, dict):
            continue
        normalized = normalize_discovery_repo(r)
        canonical = canonical_repo_id(normalized)
        short = normalized.get("repo_name", normalized.get("name", ""))
        if repo_id in (canonical, short):
            return normalized
        if "/" not in repo_id and repo_name_part == short:
            exact_matches.append(normalized)
    if len(exact_matches) == 1:
        return exact_matches[0]
    return None


def validate_repo_against_discovery(repo_id: str, discovery: dict[str, Any] | None) -> str | None:
    """Validate repo_id against discovery data. Returns error message if invalid."""
    import difflib

    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
    if not repos:
        if discovery is not None and isinstance(discovery, dict) and discovery.get("repos_scanned", 0) == 0:
            return (
                "No repositories found in discovery. Run discovery for this profile before migrating."
            )
        return None
    if find_discovery_repo(repo_id, repos):
        return None
    valid_names = sorted(discovery_repo_names(repos))
    repo_name_part = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    close = difflib.get_close_matches(repo_id, valid_names, n=3, cutoff=0.4)
    if not close:
        close = difflib.get_close_matches(repo_name_part, valid_names, n=3, cutoff=0.4)
    if close:
        suggestions = ", ".join(f"'{c}'" for c in close)
        return f"Repository '{repo_id}' was not found. Did you mean: {suggestions}?"
    return (
        f"Repository '{repo_id}' is not a valid repository name. "
        "Please enter the repository as Project/RepoName."
    )


def repo_not_found_clarification(repo_id: str, discovery: dict[str, Any] | None) -> dict[str, Any]:
    """Build a pending_clarification payload for an unknown repository."""
    import difflib

    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
    valid_names = sorted(discovery_repo_names(repos)) if repos else []
    repo_name_part = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    suggestions = difflib.get_close_matches(repo_id, valid_names, n=3, cutoff=0.4)
    if not suggestions:
        suggestions = difflib.get_close_matches(repo_name_part, valid_names, n=3, cutoff=0.4)
    message = validate_repo_against_discovery(repo_id, discovery) or (
        f"Repository '{repo_id}' was not found in discovery data."
    )
    return {
        "message_type": "repo_not_found",
        "payload": {
            "message": message,
            "suggestions": suggestions,
            "requested": repo_id,
        },
    }
