"""Shared utility functions for the migration agent.

Extracted from session_orchestrator.py and orchestration/session_state.py.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_dry_run(
    session: dict[str, Any] | None,
    *,
    migration_plan: dict[str, Any] | None = None,
    executor_result: dict[str, Any] | None = None,
) -> bool:
    """Resolve whether the current PEV cycle is dry-run (default True)."""
    if isinstance(executor_result, dict) and "dry_run" in executor_result:
        return bool(executor_result.get("dry_run"))
    if isinstance(migration_plan, dict) and "dry_run" in migration_plan:
        return bool(migration_plan.get("dry_run"))
    if isinstance(session, dict):
        return bool(session.get("dry_run", True))
    return True


def _append_event(
    session: dict[str, Any],
    *,
    role: str,
    content: str,
    kind: str = "message",
    subagent: str | None = None,
    meta: dict | None = None,
) -> None:
    """Append a message event to the session's message list."""
    entry: dict[str, Any] = {
        "role": role,
        "content": content,
        "kind": kind,
        "timestamp": _now(),
    }
    if subagent:
        entry["subagent"] = subagent
    if meta:
        entry["meta"] = meta
    session.setdefault("messages", []).append(entry)
    session["updated_at"] = _now()


def publish_orchestrator_chat(session: dict[str, Any], content: str) -> str:
    """Publish a user-facing orchestrator message (Markdown) to the chat."""
    from ado2gh.agents.migration_agent.message_format import (
        CHAT_CONTENT_FORMAT,
        normalize_chat_markdown,
    )

    markdown = normalize_chat_markdown(content)
    _append_event(
        session,
        role="assistant",
        content=markdown,
        kind="message",
        subagent="orchestrator",
        meta={"content_format": CHAT_CONTENT_FORMAT},
    )
    return markdown


def _append_and_stream(
    session: dict[str, Any],
    *,
    role: str,
    content: str,
    kind: str = "thinking",
    subagent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append event to session AND emit via LangGraph stream writer for live SSE."""
    _append_event(session, role=role, content=content, kind=kind, subagent=subagent, meta=meta)
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
        if writer:
            evt: dict[str, Any] = {"kind": kind, "content": content}
            if subagent:
                evt["subagent"] = subagent
            if meta:
                evt["meta"] = meta
            writer(evt)
    except Exception:
        pass


def _set_task(tasks: list[dict], task_id: str, status: str, detail: str = "") -> None:
    """Update a task's status in the task list."""
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = status
            if detail:
                t["detail"] = detail
            t["updated_at"] = _now()
            break


_DEFAULT_TASKS = [
    {"id": "discovery", "label": "Discovery", "status": "pending", "detail": ""},
    {"id": "plan", "label": "Plan", "status": "pending", "detail": ""},
    {"id": "execute", "label": "Execute", "status": "pending", "detail": ""},
    {"id": "validate", "label": "Validate", "status": "pending", "detail": ""},
]


def _init_tasks(session: dict[str, Any]) -> list[dict]:
    """Initialize the default task list for a session."""
    return [dict(t) for t in _DEFAULT_TASKS]


def sync_work_items_to_tasks(session: dict[str, Any], work_items: list[dict[str, Any]]) -> None:
    """Sync work item statuses into the session task list."""
    tasks = session.setdefault("tasks", [])
    if not tasks:
        tasks.extend(_init_tasks(session))
    for wi in work_items:
        wi_id = wi.get("id", "")
        wi_status = wi.get("status", "pending")
        for t in tasks:
            if t["id"] == wi_id:
                t["status"] = wi_status
                t["detail"] = wi.get("detail", "")
                t["updated_at"] = _now()
                break


MAX_STATUS_MESSAGES_PER_TURN = 40

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
    session["_status_msg_count"] = 0


def _append_status_message(
    session: dict[str, Any],
    content: str,
    *,
    subagent: str | None = None,
) -> None:
    if session.get("_status_msg_count", 0) >= MAX_STATUS_MESSAGES_PER_TURN:
        return
    session["_status_msg_count"] = int(session.get("_status_msg_count", 0)) + 1
    _append_event(
        session,
        role="assistant",
        content=content,
        kind="status",
        subagent=subagent,
    )
    # Emit via LangGraph stream writer for real-time SSE
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
        if writer:
            writer({"kind": "status", "content": content, "subagent": subagent or "orchestrator"})
    except Exception:
        pass


def _tool_status_done(tool_name: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return result.get("message") or str(result["error"])
    if tool_name == "run_migration_pev":
        mode = "dry-run" if result.get("dry_run", True) else "live"
        return f"Migration pipeline starting ({mode})."
    if tool_name == "fetch_migration_status":
        return "Migration status updated."
    if tool_name in ("invoke_planner", "invoke_bulk_planner"):
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
    _append_and_stream(
        session,
        role="system",
        content=label,
        kind="tool_call",
        subagent=subagent,
        meta=meta,
    )


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
        if writer:
            writer({
                "kind": "tool_result",
                "content": summary,
                "subagent": subagent,
                "meta": {"tool_name": tool_name, **result},
            })
    except Exception:
        pass


# ─── User message queuing during PEV execution ───

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
    """Queue a user message while PEV chain is running."""
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
    """Parse JSON from LLM output, tolerating markdown code fences and alternate formats."""
    import json
    import re

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

_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|secret|token|api[_-]?key|pat)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"(?i)Bearer\s+\S+"),
]


def mask_secrets(text: str) -> str:
    """Mask secret values in text, keeping only the key name."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda m: m.group(0).split("=")[0].split(":")[0] + "=***MASKED***"
            if "=" in m.group(0) or ":" in m.group(0)
            else "***MASKED***",
            text,
        )
    return text


# ─── Audit bridge (moved from local/audit_bridge.py) ─────────────────


class IdeAuditBridge:
    """Audit bridge for IDE sessions and MCP tool calls."""

    def __init__(self) -> None:
        self._writer = None

    def _ensure_writer(self) -> Any:
        if self._writer is None:
            from ado2gh.assignments.audit import AuditWriter
            from ado2gh.state.db import StateDB

            self._writer = AuditWriter(StateDB())
        return self._writer

    def record(
        self,
        action: str,
        *,
        profile_id: str = "",
        repo: str = "",
        detail: str = "",
        actor: str = "local-developer",
        session_id: str = "",
        assignment_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Record an audit event. Returns audit ID or None on failure."""
        try:
            writer = self._ensure_writer()
            payload: dict[str, Any] = {}
            if repo:
                payload["repo"] = repo
            if detail:
                payload["detail"] = mask_secrets(detail)
            if session_id:
                payload["session_id"] = session_id
            if assignment_id:
                payload["assignment_id"] = assignment_id
            if metadata:
                payload["metadata"] = {k: mask_secrets(str(v)) for k, v in metadata.items()}
            return writer.write(
                event_type=action,
                profile_id=profile_id,
                actor=actor,
                assignment_id=assignment_id or None,
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


async def load_discovery_snapshot(
    session: dict[str, Any],
    accel_get: Any,
    session_token: str | None = None,
) -> dict[str, Any]:
    """Load discovery data into the session when missing."""
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
        session["discovery_snapshot"] = discovery
        from datetime import datetime, timezone
        session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
        return discovery if isinstance(discovery, dict) else {}
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
