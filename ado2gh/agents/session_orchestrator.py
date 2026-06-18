"""Tool-driven PEV orchestrator — planner / executor / validator via guarded tools."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ado2gh.agents.agent_scope import (
    is_out_of_scope_message,
    scope_refusal_reply,
)
from ado2gh.agents.live_execution_policy import (
    live_execution_block_message,
    session_requires_live_approval,
)
from ado2gh.agents.execution_mode import (
    apply_execution_mode_from_message,
    parse_execution_mode_from_message,
)
from ado2gh.agents.llm_provider import LLMProvider
from ado2gh.agents.pev_coordinator import review_planner_output

PEV_TASK_TEMPLATE = [
    {"id": "discovery", "label": "Load profile discovery", "subagent": "planner"},
    {"id": "plan", "label": "Build migration plan", "subagent": "planner"},
    {"id": "execute", "label": "Execute migration pipeline", "subagent": "executor"},
    {"id": "validate", "label": "Validate migrated repos", "subagent": "validator"},
]

ORCHESTRATOR_SYSTEM = """You are the ADO→GitHub migration agent orchestrator.
You MUST use tools for migration work — never invent repo lists or phases from conversation alone.

Rules:
1. Call fetch_profile_discovery before build_migration_plan.
2. build_migration_plan requires discovery data in session state.
3. run_migration_pev requires an existing migration_plan AND plan_approved=true (operator confirmed the plan via the review form).
4. After build_migration_plan succeeds, STOP and wait for operator confirmation — do NOT call run_migration_pev in the same turn.
5. Remigrate, retry, or replan requests MUST rebuild the plan and show the confirmation form — never start execution in that turn, even if a prior plan was approved.
6. When the operator asks for live execution (e.g. "no dry run", "migrate live"), set dry_run false before planning if they are admin/approver; operators need approval.
7. NEVER call run_migration_pev for a live session when session.requires_live_approval is true (operators only — admins and approvers may run live directly).
8. Use request_user_input when phase, profile, or confirmation is missing — only once per missing field; if phase was auto-resolved, call build_migration_plan next (do not call request_user_input again for phase).
9. For general questions (time, greetings, unrelated topics), reply in plain text with tool_calls=[] — never call migration tools. For migration status, use fetch_migration_status.
10. Chain discovery → plan only until the operator confirms the plan; then run_migration_pev only after explicit execute approval (e.g. "execute", "run migration", or form confirm+execute).
11. run_migration_pev starts the executor+validator pipeline; the LLM reviews each subagent phase and may retry up to 3 times.
12. ONLY use phase values listed in session.available_phases (from discovery/settings). If the operator names a phase that is not in that list, reply with tool_calls=[] explaining the valid phases — do NOT call build_migration_plan with a guessed or default phase.
13. When the request is ambiguous, malformed, or refers to a non-existent phase, explain what is wrong and ask for clarification instead of proceeding.
14. REFUSE off-topic requests (recipes, general knowledge, hacking, illegal activity). Reply with tool_calls=[] and a brief refusal — never answer outside migration scope.

Respond with JSON only:
{
  "thinking": "optional reasoning (shown when model supports thinking)",
  "tool_calls": [{"name": "tool_name", "arguments": {}}],
  "reply": "user-facing message when no more tools needed this turn"
}
"""

GENERAL_CHAT_SYSTEM = """You are the ADO→GitHub migration assistant.
ONLY discuss Azure DevOps → GitHub migration and how to use this assistant.
Brief in-scope replies: greetings, what you can help with (discovery, planning, execution, validation).
REFUSE all other topics — recipes, general knowledge, unrelated coding, hacking, illegal activity.
Never invent migration status or repo lists. Never search the web or answer off-topic requests.
If a migration plan is waiting for review, you may remind the operator to use the confirmation form.
"""

INTENT_CLASSIFIER_SYSTEM = """Classify the user's message for an Azure DevOps → GitHub migration assistant.
Return JSON only:
{
  "intent": "out_of_scope" | "general_chat" | "migration_info" | "migration_action",
  "reason": "brief explanation"
}

- out_of_scope: unrelated topics (recipes, general knowledge, entertainment, hacking, illegal activity, web search for non-migration topics) — NOT migration work
- general_chat: brief greetings or questions about what this migration assistant can do
- migration_info: questions ABOUT the migration (status, plan summary, what will happen, tell me about, overview) — user is NOT asking to plan, scan, execute, or change configuration
- migration_action: user wants to build/replan/execute migration, scan/discover/inventory, retry/remigrate, approve plan, or change migration settings

When unsure between migration_info and migration_action, prefer migration_info if the user only wants to understand or summarize.
When the request is clearly unrelated to ADO/GitHub migration, use out_of_scope."""

MIGRATION_INFO_SYSTEM = """You answer questions about the ADO→GitHub migration using ONLY the provided context.
- Distinguish what is planned vs what has actually been executed/migrated.
- Summarize clearly: phase, repos, dry-run vs live, approval state.
- Do NOT rebuild plans or prompt confirmation forms unless the user asked how to proceed with execution.
- If no plan exists yet, say so and mention they can ask to build a migration plan when ready.
- Be concise and accurate; never invent repos or counts not in context."""

INTERNAL_TOOLS = {
    "fetch_profile_discovery": {
        "subagent": "planner",
        "description": "Load repos, phases, risk scores, and ADO inventory from the active profile.",
    },
    "run_profile_scan": {
        "subagent": "planner",
        "description": (
            "Re-scan ADO org including pipeline inventory, service connections, and variable groups. "
            "Use when pipeline inventory is empty or secrets/pipeline steps are blocked."
        ),
    },
    "build_migration_plan": {
        "subagent": "planner",
        "description": "Run planner subagent using discovery data for a phase.",
        "arguments": {"phase": "poc|pilot|wave1|..."},
    },
    "run_migration_pev": {
        "subagent": "executor",
        "description": "Start executor+validator pipeline (dry-run by default).",
    },
    "fetch_migration_status": {
        "subagent": "validator",
        "description": (
            "Report which repos have actually been migrated (git/pipeline state), "
            "including failures — not the in-chat plan."
        ),
    },
    "request_user_input": {
        "subagent": "planner",
        "description": "Present a form when required fields are missing.",
    },
}


@dataclass
class OrchestratorResult:
    reply: str = ""
    start_pev: bool = False
    tasks: list[dict[str, Any]] = field(default_factory=list)
    pending_form: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tasks() -> list[dict[str, Any]]:
    return [
        {**t, "status": "pending", "updated_at": _now()}
        for t in PEV_TASK_TEMPLATE
    ]


def _set_task(tasks: list[dict], task_id: str, status: str, detail: str = "") -> None:
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = status
            if detail:
                t["detail"] = detail
            t["updated_at"] = _now()
            break


PEV_TASK_IDS = frozenset(t["id"] for t in PEV_TASK_TEMPLATE)


def sync_work_items_to_tasks(session: dict[str, Any], work_items: list[dict[str, Any]]) -> None:
    """Append scope-level summary tasks (counts, not per-repo rows) after core PEV tasks."""
    from ado2gh.api.migration_work_plan import aggregate_work_items_for_timeline

    tasks = session.get("tasks")
    if not tasks:
        tasks = _init_tasks()
    session["tasks"] = [t for t in tasks if t["id"] in PEV_TASK_IDS]
    for row in aggregate_work_items_for_timeline(work_items):
        session["tasks"].append({
            "id": row["id"],
            "label": row["label"],
            "subagent": {
                "migrate_repo": "executor",
                "convert_metadata": "executor",
                "manual_setup": "planner",
            }.get(row.get("category", ""), "executor"),
            "category": row.get("category"),
            "category_label": row.get("category_label"),
            "status": row.get("status", "pending"),
            "detail": row.get("blocker") or row.get("description", ""),
            "blocker": row.get("blocker"),
            "count": row.get("count", 0),
            "updated_at": _now(),
        })


def _append_event(
    session: dict[str, Any],
    *,
    role: str,
    content: str,
    kind: str = "message",
    subagent: str | None = None,
    meta: dict | None = None,
) -> None:
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


def _parse_llm_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"reply": text, "tool_calls": []}


def _migration_status_intent(user_message: str) -> bool:
    msg = user_message.lower()
    status_phrases = (
        "which repos",
        "what repos",
        "how many repos",
        "migrated so far",
        "already migrated",
        "migration status",
        "migration progress",
        "migration history",
        "what has been migrated",
        "what's been migrated",
        "what has migrated",
        "repos have been",
        "repos were migrated",
        "show migrated",
        "list migrated",
        "completed repos",
        "failed repos",
        "migration monitor",
        "run status",
        "pipeline status",
    )
    if any(p in msg for p in status_phrases):
        return True
    if "migrat" in msg and any(
        w in msg for w in ("status", "progress", "so far", "already", "which", "what", "how many", "list", "show")
    ):
        return True
    return False


def _migration_action_signals(user_message: str) -> bool:
    return any(
        (
            _user_wants_retry_migration(user_message),
            _user_wants_replan_only(user_message),
            _wants_migration_execute(user_message),
            _user_approves_plan(user_message),
            _user_requests_plan_changes(user_message),
            _explicit_migration_action_phrases(user_message),
        )
    )


def _explicit_migration_action_phrases(user_message: str) -> bool:
    msg = user_message.lower()
    action_phrases = (
        "migrate ",
        "migrating ",
        "remigrate",
        "re-migrate",
        "to migrate",
        "please migrate",
        "migration plan",
        "build plan",
        "build the plan",
        "build migration",
        "build a migration",
        "create a plan",
        "make a plan",
        "plan migration",
        "dry-run",
        "dry run",
        "discover ",
        "discovery",
        "inventory",
        "service connection",
        "pipeline inventory",
        "scan ado",
        "scan the org",
        "re-scan",
        "rescan",
        "execute",
        "start migration",
        "run migration",
        "pev",
    )
    if any(p in msg for p in action_phrases):
        return True
    if "plan" in msg and any(w in msg for w in ("build", "create", "make", "poc", "pilot", "wave", "phase")):
        return True
    if "scan" in msg and any(w in msg for w in ("ado", "org", "profile", "inventory")):
        return True
    return False


def _migration_info_intent(user_message: str) -> bool:
    """Informational questions about migration state/plan — not requests to plan or execute."""
    if _migration_action_signals(user_message):
        return False
    msg = user_message.lower()
    info_phrases = (
        "tell me about",
        "tell me more",
        "what is the migration",
        "what's the migration",
        "about the migration",
        "about this migration",
        "describe the migration",
        "summarize the migration",
        "summary of the migration",
        "explain the migration",
        "what does the plan",
        "what's in the plan",
        "what is the plan",
        "current plan",
        "migration overview",
        "overview of the migration",
        "how does the migration work",
        "what will be migrated",
        "what are we migrating",
    )
    if any(p in msg for p in info_phrases):
        return True
    if _migration_status_intent(user_message):
        return True
    if "migration" in msg and any(
        w in msg
        for w in ("tell", "about", "describe", "explain", "summarize", "overview", "what is", "what's", "how does")
    ):
        return True
    return False


def _migration_action_intent(user_message: str) -> bool:
    """True when the user wants planning, scanning, execution, or plan changes."""
    return _migration_action_signals(user_message)


def _migration_intent(user_message: str) -> bool:
    """Backward-compatible alias for migration action intent."""
    return _migration_action_intent(user_message)


def _classify_user_intent_heuristic(user_message: str, session: dict[str, Any]) -> str:
    del session  # reserved for future session-aware heuristics
    if is_out_of_scope_message(user_message):
        return "out_of_scope"
    if _migration_action_intent(user_message):
        return "migration_action"
    if _migration_info_intent(user_message):
        return "migration_info"
    return "general_chat"


def _classify_user_intent(
    user_message: str,
    session: dict[str, Any],
    llm: LLMProvider,
    *,
    llm_degraded: bool,
) -> str:
    if llm_degraded:
        return _classify_user_intent_heuristic(user_message, session)
    context = {
        "has_plan": bool(session.get("migration_plan")),
        "plan_approved": bool(session.get("plan_approved")),
        "pending_form": bool(session.get("pending_form")),
    }
    prompt = f"User message: {user_message!r}\n\nSession hints: {json.dumps(context)}"
    raw = llm.complete(prompt, system=INTENT_CLASSIFIER_SYSTEM)
    parsed = _parse_llm_json(raw)
    intent = str(parsed.get("intent", "")).lower().replace("-", "_")
    if intent in ("out_of_scope", "general_chat", "migration_info", "migration_action"):
        if intent != "out_of_scope" and is_out_of_scope_message(user_message):
            return "out_of_scope"
        return intent
    return _classify_user_intent_heuristic(user_message, session)


def _migration_workflow_active(user_message: str) -> bool:
    """True when the user is asking for migration planning/execution work."""
    return _migration_action_intent(user_message)


def _general_chat_reply(
    user_message: str,
    llm: LLMProvider,
    session: dict[str, Any],
) -> str:
    if is_out_of_scope_message(user_message):
        return scope_refusal_reply(user_message)
    raw = llm.complete(user_message, system=GENERAL_CHAT_SYSTEM)
    parsed = _parse_llm_json(raw)
    reply = (parsed.get("reply") or raw or "").strip()
    if session.get("pending_form") and "form" not in reply.lower():
        reply += "\n\nA migration plan is waiting for your review in the form below."
    return reply


async def _migration_info_reply(
    user_message: str,
    session: dict[str, Any],
    llm: LLMProvider,
    accel_get: Callable[..., Awaitable[dict]],
    session_token: str | None,
    *,
    llm_degraded: bool,
) -> str:
    status_result = await _tool_fetch_migration_status(accel_get, session_token)
    plan = session.get("migration_plan") or {}
    context = {
        "execution": {
            "summary": status_result.get("summary"),
            "narrative": status_result.get("narrative"),
            "migrated_count": status_result.get("migrated_count"),
            "failed_count": status_result.get("failed_count"),
        },
        "session_plan": {
            "exists": bool(plan),
            "phase": plan.get("phase") or session.get("plan_phase"),
            "repo_count": plan.get("repo_count"),
            "dry_run": session.get("dry_run", True),
            "plan_approved": bool(session.get("plan_approved")),
            "blocked": plan.get("blocked"),
            "narrative": plan.get("narrative"),
            "repos": (plan.get("repos") or [])[:20],
            "pipeline_steps": plan.get("pipeline_steps"),
            "work_items_ready": plan.get("work_items_ready"),
            "work_items_skipped": plan.get("work_items_skipped"),
        },
        "pending_review_form": bool(session.get("pending_form")),
    }
    if llm_degraded:
        parts: list[str] = []
        msg = user_message.lower()
        status_only = _migration_status_intent(user_message) and not any(
            p in msg for p in ("tell me", "about the migration", "about this migration", "overview")
        )
        if plan and not status_only:
            parts.append(
                plan.get("narrative")
                or f"Session plan: phase {context['session_plan']['phase']}, "
                f"{context['session_plan']['repo_count'] or 0} repo(s), "
                f"{'dry-run' if context['session_plan']['dry_run'] else 'live'}."
            )
        narrative = status_result.get("narrative")
        if narrative:
            parts.append(narrative)
        if not parts:
            parts.append(
                "No migration plan has been built in this session yet. "
                "Ask to build a migration plan when you are ready to configure a run."
            )
        reply = "\n\n".join(parts)
    else:
        raw = llm.complete(
            f"User question: {user_message}\n\nContext:\n{json.dumps(context, indent=2, default=str)}",
            system=MIGRATION_INFO_SYSTEM,
        )
        parsed = _parse_llm_json(raw)
        reply = (parsed.get("reply") or raw or "").strip()
    if session.get("pending_form") and "form" not in reply.lower() and "review" not in reply.lower():
        reply += (
            "\n\nA migration plan review form is available below if you want to "
            "confirm or change settings before execution."
        )
    return reply


def _user_wants_replan_only(user_message: str) -> bool:
    msg = user_message.lower()
    return any(
        w in msg
        for w in (
            "remigrate",
            "re-migrate",
            "re migrate",
            "replan",
            "re-plan",
        )
    )


def _user_wants_retry_migration(user_message: str) -> bool:
    msg = user_message.lower()
    if _user_wants_replan_only(user_message):
        return False
    return any(
        w in msg
        for w in (
            "retry",
            "try again",
            "re-run",
            "rerun",
            "run again",
            "deleted",
            "repo is gone",
            "migrate again",
            "run migration again",
        )
    )


def _discovery_needs_fetch(session: dict[str, Any]) -> bool:
    snap = session.get("discovery_snapshot") or {}
    if not snap:
        return True
    repos = snap.get("repos")
    if repos is None:
        return True
    if not repos and int(snap.get("repos_scanned") or 0) > 0:
        return True
    return False


def _available_migration_phases(session: dict[str, Any]) -> list[str]:
    snap = session.get("discovery_snapshot") or {}
    recs = snap.get("recommendations") or {}
    if recs:
        return sorted(recs.keys())
    from_repos = {
        (r.get("assigned_phase") or r.get("suggested_phase") or "").strip().lower()
        for r in (snap.get("repos") or [])
        if r
    }
    from_repos.discard("")
    if from_repos:
        return sorted(from_repos)
    from ado2gh.api.phase_definitions import default_phase_definitions

    return [p.id for p in default_phase_definitions()]


def _extract_requested_phase(user_message: str) -> str | None:
    msg = user_message.lower()
    patterns = (
        r"migrate\s+(?:the\s+)?([a-z0-9][a-z0-9_-]*)\s+phase",
        r"for\s+(?:the\s+)?([a-z0-9][a-z0-9_-]*)\s+phase",
        r"in\s+(?:the\s+)?([a-z0-9][a-z0-9_-]*)\s+phase",
        r"phase\s+([a-z0-9][a-z0-9_-]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, msg)
        if match:
            return match.group(1)
    return None


def _resolve_migration_phase(
    user_message: str,
    session: dict[str, Any],
    *,
    explicit_phase: str | None = None,
) -> tuple[str | None, str | None]:
    """Return (phase, error_message). error_message is set when the phase is invalid."""
    available = _available_migration_phases(session)
    candidate = (explicit_phase or _extract_requested_phase(user_message) or "").strip().lower() or None
    if candidate:
        if candidate in available:
            return candidate, None
        listed = ", ".join(f"`{p}`" for p in available)
        return None, (
            f"Unknown migration phase `{candidate}`. "
            f"Valid phases for this profile: {listed}."
        )

    msg = user_message.lower()
    for phase in available:
        if re.search(rf"\b{re.escape(phase)}\b", msg):
            return phase, None

    default = (session.get("plan_phase") or "poc").strip().lower()
    if default in available:
        return default, None
    return (available[0] if available else "poc"), None


def _phase_from_message(user_message: str, session: dict[str, Any]) -> str:
    phase, err = _resolve_migration_phase(user_message, session)
    if err:
        return session.get("plan_phase", "poc")
    return phase or session.get("plan_phase", "poc")


def _wants_migration_execute(user_message: str) -> bool:
    if _user_wants_retry_migration(user_message) or _user_wants_replan_only(user_message):
        return False
    if _user_requests_plan_changes(user_message):
        return False
    msg = user_message.lower()
    explicit_execute = (
        "execute",
        "start migration",
        "run migration",
        "run the migration",
        "start the migration",
        "go ahead",
        "proceed with migration",
        "proceed with execution",
        "dry-run",
        "dry run",
    )
    if any(p in msg for p in explicit_execute):
        return True
    if msg.strip() in ("yes", "go", "start", "run"):
        return True
    if parse_execution_mode_from_message(user_message) is False and any(
        w in msg for w in ("execute", "run", "start", "go")
    ):
        return True
    return False


def _migration_tool_calls(session: dict[str, Any], user_message: str) -> list[dict[str, Any]]:
    """Deterministic discovery → plan → execute chain (one tool per stub iteration)."""
    snap = session.get("discovery_snapshot") or {}
    inventory_count = int(snap.get("pipeline_inventory_count") or 0)
    retry = bool(session.get("migration_retry"))
    replan = bool(session.get("migration_replan"))
    msg = user_message.lower()
    explicit_inventory = any(
        w in msg
        for w in (
            "inventory",
            "service connection",
            "pipeline inventory",
            "discover service",
            "scan ado",
            "re-scan",
            "rescan",
            "deleted",
        )
    )
    if retry:
        return [{"name": "run_profile_scan", "arguments": {}}]
    if replan:
        if not session.get("migration_plan"):
            phase = _phase_from_message(user_message, session)
            return [{"name": "build_migration_plan", "arguments": {"phase": phase}}]
        return []
    if not snap:
        return [{"name": "fetch_profile_discovery", "arguments": {}}]
    if explicit_inventory and inventory_count == 0:
        return [{"name": "run_profile_scan", "arguments": {}}]
    if _discovery_needs_fetch(session):
        return [{"name": "fetch_profile_discovery", "arguments": {}}]
    if not session.get("migration_plan") or retry:
        if session.get("migration_plan") and retry:
            session.pop("migration_plan", None)
            session["plan_approved"] = False
        phase = _phase_from_message(user_message, session)
        return [{"name": "build_migration_plan", "arguments": {"phase": phase}}]
    if session.get("plan_approved") and _wants_migration_execute(user_message):
        if session.get("migration_retry"):
            return []
        return [{"name": "run_migration_pev", "arguments": {}}]
    return []


def _auto_migration_chain_step(session: dict[str, Any], user_message: str) -> dict[str, Any] | None:
    """Next migration tool to run automatically after scan/discovery without waiting for the LLM."""
    replan = bool(session.get("migration_replan"))
    if not replan and _discovery_needs_fetch(session):
        return {"name": "fetch_profile_discovery", "arguments": {}}
    snap = session.get("discovery_snapshot") or {}
    if not snap:
        return None
    retry = bool(session.get("migration_retry"))
    if replan and not session.get("migration_plan"):
        phase = _phase_from_message(user_message, session)
        return {"name": "build_migration_plan", "arguments": {"phase": phase}}
    if not session.get("migration_plan") or retry:
        if session.get("migration_plan") and retry:
            session.pop("migration_plan", None)
            session["plan_approved"] = False
        phase = _phase_from_message(user_message, session)
        return {"name": "build_migration_plan", "arguments": {"phase": phase}}
    return None


def _retry_chain_prefix(session: dict[str, Any]) -> str:
    if session.get("migration_retry"):
        return (
            "**Retry:** rescanned discovery and rebuilt the migration plan "
            "after the target repo was removed.\n\n"
        )
    if session.get("migration_replan"):
        return (
            "**Remigration:** rebuilt the migration plan from current discovery data. "
            "Review the configuration before executing.\n\n"
        )
    return ""


def _user_approves_plan(user_message: str) -> bool:
    msg = user_message.lower()
    return any(
        p in msg
        for p in (
            "approve the plan",
            "plan looks good",
            "plan is correct",
            "confirm the plan",
            "proceed with the plan",
            "plan is good",
            "looks correct",
        )
    )


def _user_requests_plan_changes(user_message: str) -> bool:
    msg = user_message.lower()
    return any(
        p in msg
        for p in (
            "change phase",
            "different phase",
            "wrong repo",
            "exclude ",
            "add repo",
            "replan",
            "re-plan",
            "update the plan",
            "change the plan",
        )
    )


def _inventory_gaps_form(session: dict[str, Any]) -> dict[str, Any] | None:
    from ado2gh.api.migration_work_plan import collect_secret_gap_fields

    plan = session.get("migration_plan") or {}
    work_items = plan.get("work_items") or []
    discovery = session.get("discovery_snapshot") or {}
    fields = collect_secret_gap_fields(discovery, work_items)
    if not fields:
        return None
    return {
        "form_id": "inventory_gaps",
        "title": "Service connection mappings required",
        "description": (
            "ADO service connection secret values cannot be read from Azure DevOps. "
            "Provide the GitHub secret name (or OIDC credential id) to use for each connection "
            "before pipeline conversion and secrets mapping can proceed."
        ),
        "fields": fields,
        "submit_action": "inventory_gaps",
    }


def _plan_confirmation_form(session: dict[str, Any]) -> dict[str, Any]:
    plan = session.get("migration_plan") or {}
    phase = plan.get("phase") or session.get("plan_phase", "poc")
    mode = "dry-run" if session.get("dry_run", True) else "live"
    repos = plan.get("repos") or []
    steps = plan.get("pipeline_steps") or ["connect", "migrate", "validate"]
    narrative = (plan.get("narrative") or "").strip()
    repo_count = plan.get("repo_count", len(repos))

    description_parts = [
        "### Migration summary",
        "",
        f"- **Phase:** {phase}",
        f"- **Mode:** {mode}",
        f"- **Repos:** {repo_count}",
        "",
        "### Pipeline",
        "",
        " → ".join(f"`{step}`" for step in steps),
        "",
    ]
    if repos:
        description_parts.extend(["### Repositories", ""])
        for repo in repos[:15]:
            description_parts.append(f"- `{repo}`")
        if len(repos) > 15:
            description_parts.append(f"- … and {len(repos) - 15} more")
        description_parts.append("")
    if narrative:
        description_parts.extend(["### Plan details", "", narrative, ""])
    description_parts.extend([
        "---",
        "",
        "Confirm the plan is correct, or describe changes in **Notes** "
        "(leave confirmation unchecked to request a replan).",
    ])
    description = "\n".join(description_parts)

    return {
        "form_id": "plan_confirmation",
        "title": "Review migration plan",
        "description": description,
        "fields": [
            {
                "name": "plan_confirmed",
                "label": "Plan is correct — proceed when I run execution",
                "type": "checkbox",
                "required": False,
            },
            {
                "name": "plan_notes",
                "label": "Changes or questions (optional)",
                "type": "textarea",
                "required": False,
            },
            {
                "name": "confirm_execute",
                "label": (
                    f"Start {mode} migration immediately after confirming"
                ),
                "type": "checkbox",
                "required": False,
            },
        ],
        "submit_action": "plan_confirmation",
    }


def _plan_confirmation_reply(session: dict[str, Any]) -> str:
    plan = session.get("migration_plan") or {}
    if plan.get("blocked"):
        return plan.get("block_reason", "Migration plan is blocked.")
    narrative = plan.get("narrative")
    prefix = (
        "I've prepared a migration plan. **Please review the configuration** — "
        "confirm the phase, repos, GitHub targets, and scopes are correct before execution.\n\n"
    )
    if narrative:
        return prefix + str(narrative) + "\n\nUse the form below to confirm or describe changes."
    return prefix + migration_ready_reply(session)


def migration_ready_reply(session: dict[str, Any]) -> str:
    plan = session.get("migration_plan") or {}
    if plan.get("blocked"):
        return plan.get("block_reason", "Migration plan is blocked.")
    narrative = plan.get("narrative")
    if narrative:
        return str(narrative)
    count = plan.get("repo_count", 0)
    phase = plan.get("phase", session.get("plan_phase", "poc"))
    mode = "dry-run" if session.get("dry_run", True) else "live"
    if not session.get("dry_run", True) and session_requires_live_approval(session):
        role = session.get("user_role", "operator")
        return (
            f"Migration plan ready for phase {phase} ({count} repo(s), live). "
            f"As **{role}**, you need platform approval before live execution can start. "
            "Request approval or switch to a dry-run."
        )
    if not session.get("plan_approved"):
        return (
            f"Migration plan ready for phase {phase} ({count} repo(s), {mode}). "
            "**Confirm the plan** in the form below (or describe changes) before execution starts."
        )
    return (
        f"Plan confirmed for phase {phase} ({count} repo(s), {mode}). "
        + (
            "Say **execute** or **migrate live** to run the pipeline."
            if not session.get("dry_run", True)
            else "Say **execute dry-run** to run the pipeline, or switch to **live** mode to migrate for real."
        )
    )


def _stub_orchestrate(user_message: str, session: dict[str, Any]) -> dict[str, Any]:
    """Deterministic tool routing when LLM is stub/degraded."""
    intent = _classify_user_intent_heuristic(user_message, session)
    if intent == "general_chat":
        return {
            "thinking": "General question — answering without migration tools.",
            "tool_calls": [],
            "reply": "",
        }
    if intent == "migration_info":
        return {
            "thinking": "Informational migration question — summarizing without rebuilding plan.",
            "tool_calls": [],
            "reply": "",
        }
    if _migration_action_intent(user_message):
        extracted = _extract_requested_phase(user_message)
        if extracted:
            _, phase_err = _resolve_migration_phase(user_message, session)
            if phase_err:
                return {
                    "thinking": "Invalid migration phase — not proceeding.",
                    "tool_calls": [],
                    "reply": phase_err,
                }
        calls = _migration_tool_calls(session, user_message)
        if not calls:
            return {
                "thinking": "Migration plan complete; awaiting execute confirmation.",
                "tool_calls": [],
                "reply": migration_ready_reply(session),
            }
        return {
            "thinking": f"Running migration tool: {calls[0]['name']}",
            "tool_calls": calls,
            "reply": "",
        }
    return {
        "thinking": "Answering without migration tools.",
        "tool_calls": [],
        "reply": f"[stub] {user_message[:120]}",
    }


async def _tool_fetch_migration_status(
    accel_get: Callable[..., Awaitable[dict]],
    session_token: str | None,
) -> dict[str, Any]:
    from ado2gh.api.migration_status_report import (
        build_migration_status_report,
        format_migration_status_narrative,
    )

    data = await accel_get("/v1/migration/status", session_token=session_token)
    narrative = format_migration_status_narrative(data)
    return {
        "summary": data.get("summary"),
        "migrated_count": len(data.get("migrated_repos") or []),
        "failed_count": len(data.get("failed_repos") or []),
        "narrative": narrative,
    }


async def _tool_run_profile_scan(
    session: dict[str, Any],
    accel_post: Callable[..., Awaitable[dict]],
    session_token: str | None,
) -> dict[str, Any]:
    profile_id = session.get("profile_id")
    result = await accel_post(
        f"/v1/settings/profiles/{profile_id}/scan?sync=true",
        {},
        session_token=session_token,
    )
    session["discovery_snapshot"] = {
        "profile_id": profile_id,
        "scanned_at": result.get("scanned_at", ""),
        "repos_scanned": result.get("repos_scanned", 0),
        "projects_scanned": result.get("projects_scanned", 0),
        "repos": [],
        "org_inventory": result.get("org_inventory", {}),
        "pipeline_inventory_count": (result.get("org_inventory") or {}).get("pipeline_inventory_count")
        or (result.get("pipeline_inventory") or {}).get("inventory_count")
        or 0,
        "inventory_gaps": result.get("inventory_gaps", []),
        "project_details": result.get("project_details", []),
        "warnings": result.get("warnings", []),
        "status": result.get("status", "ok"),
    }
    session["discovery_fetched_at"] = _now()
    return {
        "repos_scanned": result.get("repos_scanned", 0),
        "projects_scanned": result.get("projects_scanned", 0),
        "pipeline_inventory_count": session["discovery_snapshot"].get("pipeline_inventory_count", 0),
        "service_connections": (result.get("org_inventory") or {}).get("total_service_connections", 0),
    }


async def _tool_fetch_discovery(
    session: dict[str, Any],
    accel_get: Callable[..., Awaitable[dict]],
    session_token: str | None,
) -> dict[str, Any]:
    profile_id = session.get("profile_id")
    data = await accel_get(
        f"/v1/settings/profiles/{profile_id}/discovery",
        session_token=session_token,
    )
    repos = data.get("repos") or []
    session["discovery_snapshot"] = data
    session["discovery_fetched_at"] = _now()
    return {
        "repos_scanned": data.get("repos_scanned", len(repos)),
        "projects_scanned": data.get("projects_scanned", 0),
        "pipeline_inventory_count": data.get("pipeline_inventory_count", 0),
        "phases": list({r.get("assigned_phase") or r.get("suggested_phase") for r in repos if r}),
    }


async def _tool_build_plan(
    session: dict[str, Any],
    arguments: dict[str, Any],
    build_plan: Callable[..., Awaitable[dict]],
    session_token: str | None,
    llm: LLMProvider,
    *,
    llm_degraded: bool = False,
    user_message: str = "",
) -> dict[str, Any]:
    if not session.get("discovery_snapshot"):
        return {
            "error": "discovery_required",
            "message": "Call fetch_profile_discovery before build_migration_plan.",
        }
    phase_arg = arguments.get("phase")
    phase, phase_err = _resolve_migration_phase(
        user_message or session.get("_last_user_message", ""),
        session,
        explicit_phase=str(phase_arg) if phase_arg else None,
    )
    if phase_err:
        return {
            "error": "invalid_phase",
            "message": phase_err,
            "available_phases": _available_migration_phases(session),
        }
    session["plan_phase"] = phase
    plan = await build_plan(session, session_token, phase=phase)
    if plan.get("work_items"):
        sync_work_items_to_tasks(session, plan["work_items"])
    review = review_planner_output(llm, plan, llm_degraded=llm_degraded)
    plan["planner_review"] = review.to_dict()
    plan["narrative"] = review.summary
    if review.next_action == "abort" and not plan.get("blocked"):
        plan["blocked"] = True
        plan["block_reason"] = review.summary
    session["migration_plan"] = plan
    session["plan_approved"] = False
    return {
        "phase": phase,
        "repo_count": plan.get("repo_count", 0),
        "blocked": plan.get("blocked", False),
        "work_summary": plan.get("work_summary"),
        "planner_review": review.to_dict(),
    }


def _user_input_auto_resolve_message(
    tool_result: dict[str, Any],
    arguments: dict[str, Any],
    session: dict[str, Any],
) -> str:
    reason = arguments.get("reason", "missing_information")
    phase = tool_result.get("phase")
    reason_labels = {
        "missing_phase": "migration phase",
        "missing_information": "required information",
    }
    need = reason_labels.get(reason, reason.replace("_", " "))
    mode = "dry-run" if session.get("dry_run", True) else "live"
    if phase:
        if not session.get("migration_plan"):
            return (
                f"**Resolved:** using migration phase `{phase}` ({mode} mode). "
                "Building the migration plan next…"
            )
        return (
            f"**Resolved:** using migration phase `{phase}`. "
            "Continuing with your request…"
        )
    return f"**Resolved:** {need} filled in automatically. Continuing…"


def _finalize_build_migration_plan(
    session: dict[str, Any],
    result: OrchestratorResult,
    tool_result: dict[str, Any],
    *,
    prefix: str = "",
) -> bool:
    """Present plan outcome to the operator. Returns True when the turn should end."""
    if tool_result.get("error"):
        err = tool_result.get("message", tool_result["error"])
        result.reply = f"{prefix}{err}".strip() if prefix else str(err)
        _append_event(session, role="assistant", content=result.reply, kind="message")
        session["subagent"] = None
        return True

    plan = session.get("migration_plan") or {}
    if plan.get("blocked"):
        blocked = plan.get("block_reason", "Migration plan is blocked.")
        result.reply = f"{prefix}{blocked}".strip() if prefix else str(blocked)
        _append_event(session, role="assistant", content=result.reply, kind="message")
        session["subagent"] = None
        return True

    gaps_form = _inventory_gaps_form(session)
    if gaps_form and not session.get("operator_secret_mappings"):
        session["pending_form"] = gaps_form
        result.pending_form = gaps_form
        body = (
            "Migration plan is ready but service connections need GitHub secret names. "
            "Complete the form below, then confirm the plan."
        )
        result.reply = f"{prefix}{body}".strip() if prefix else body
        _append_event(session, role="assistant", content=result.reply, kind="message")
        session["subagent"] = None
        return True

    form = _plan_confirmation_form(session)
    session["pending_form"] = form
    result.pending_form = form
    body = _plan_confirmation_reply(session)
    result.reply = f"{prefix}{body}".strip() if prefix else body
    _append_event(session, role="assistant", content=result.reply, kind="message")
    session["subagent"] = None
    return True


def _orchestrator_fallback_reply(session: dict[str, Any]) -> str:
    if session.get("pending_form"):
        return "Please complete the form below so I can continue."
    if not session.get("discovery_snapshot"):
        return (
            "Discovery data is not loaded yet. "
            "Ask me to scan the ADO org or load discovery for the active profile."
        )
    if not session.get("migration_plan"):
        mode = "dry-run" if session.get("dry_run", True) else "live"
        phase = session.get("plan_phase", "poc")
        return (
            f"No migration plan is built yet ({mode} mode). "
            f"I can build a plan for phase `{phase}` — confirm the phase or say **build the plan**."
        )
    if not session.get("plan_approved"):
        return migration_ready_reply(session)
    return "Plan is confirmed. Say **execute** or use the form to start the migration pipeline."


def _tool_request_form(session: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    reason = arguments.get("reason", "missing_information")
    phases = ["poc", "pilot", "wave1", "wave2", "wave3"]
    snap = session.get("discovery_snapshot") or {}
    repos = snap.get("repos") or []
    phase_opts = sorted({
        r.get("assigned_phase") or r.get("suggested_phase")
        for r in repos
        if r.get("assigned_phase") or r.get("suggested_phase")
    }) or phases
    if repos and not arguments.get("force_form"):
        chosen = session.get("plan_phase")
        if chosen and chosen in phase_opts:
            session["plan_phase"] = chosen
            return {
                "auto_resolved": True,
                "phase": chosen,
                "message": f"Using phase {chosen} from session.",
            }
        if len(phase_opts) == 1:
            session["plan_phase"] = phase_opts[0]
            return {
                "auto_resolved": True,
                "phase": phase_opts[0],
                "message": f"Using phase {phase_opts[0]} from discovery.",
            }
        if "poc" in phase_opts:
            session["plan_phase"] = "poc"
            return {
                "auto_resolved": True,
                "phase": "poc",
                "message": "Defaulting to poc phase from discovery.",
            }
    prompt = arguments.get("prompt", "").strip()
    form = {
        "form_id": arguments.get("form_id", reason),
        "title": arguments.get("title", "Additional information required"),
        "description": arguments.get("description") or prompt or (
            "The agent needs this before continuing with planner/executor tools."
        ),
        "fields": arguments.get("fields") or [
            {
                "name": "phase",
                "label": "Migration phase",
                "type": "select",
                "options": phase_opts,
                "required": True,
            },
            {
                "name": "confirm_execute",
                "label": (
                    "Execute live migration after planning"
                    if not session.get("dry_run", True)
                    else "Execute dry-run after planning"
                ),
                "type": "checkbox",
                "required": False,
            },
        ],
        "submit_action": "continue_orchestration",
    }
    session["pending_form"] = form
    return form


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session: dict[str, Any],
    accel_get: Callable[..., Awaitable[dict]],
    accel_post: Callable[..., Awaitable[dict]] | None = None,
    build_plan: Callable[..., Awaitable[dict]],
    session_token: str | None,
    llm: LLMProvider,
    llm_degraded: bool = False,
) -> dict[str, Any]:
    if name == "fetch_migration_status":
        return await _tool_fetch_migration_status(accel_get, session_token)
    if name == "fetch_profile_discovery":
        return await _tool_fetch_discovery(session, accel_get, session_token)
    if name == "run_profile_scan":
        if not accel_post:
            return {"error": "scan_unavailable", "message": "Profile scan is not available."}
        return await _tool_run_profile_scan(session, accel_post, session_token)
    if name == "build_migration_plan":
        return await _tool_build_plan(
            session,
            arguments,
            build_plan,
            session_token,
            llm,
            llm_degraded=llm_degraded,
            user_message=session.get("_last_user_message", ""),
        )
    if name == "run_migration_pev":
        plan = session.get("migration_plan")
        if not plan:
            return {"error": "plan_required", "message": "build_migration_plan must run first."}
        if plan.get("blocked"):
            return {"error": "plan_blocked", "message": plan.get("block_reason", "blocked")}
        if not session.get("plan_approved"):
            return {
                "error": "plan_not_confirmed",
                "message": (
                    "Migration plan must be confirmed by the operator before execution. "
                    "Review the plan summary and use the confirmation form, or say the plan "
                    "is correct then execute."
                ),
            }
        if session_requires_live_approval(session):
            return {
                "error": "approval_required",
                "message": live_execution_block_message(session),
            }
        return {"status": "starting", "dry_run": session.get("dry_run", True)}
    if name == "request_user_input":
        return _tool_request_form(session, arguments)
    return {"error": "unknown_tool", "name": name}


async def _auto_chain_migration_tools(
    session: dict[str, Any],
    user_message: str,
    result: OrchestratorResult,
    *,
    accel_get: Callable[..., Awaitable[dict]],
    accel_post: Callable[..., Awaitable[dict]] | None,
    build_plan: Callable[..., Awaitable[dict]],
    session_token: str | None,
    llm: LLMProvider,
    llm_degraded: bool,
) -> tuple[bool, bool]:
    """Run scan→discovery→plan chain steps. Returns (should_return, start_pev)."""
    prefix = _retry_chain_prefix(session)
    start_pev = False

    while True:
        nxt = _auto_migration_chain_step(session, user_message)
        if not nxt:
            return False, start_pev

        tool_name = nxt.get("name", "")
        args = nxt.get("arguments") or {}
        tool_meta = INTERNAL_TOOLS.get(tool_name, {})
        subagent = tool_meta.get("subagent", "planner")
        session["subagent"] = subagent

        _append_event(
            session,
            role="assistant",
            content=tool_name,
            kind="tool_call",
            subagent=subagent,
            meta={"arguments": args},
        )

        task_id = {
            "fetch_profile_discovery": "discovery",
            "run_profile_scan": "discovery",
            "build_migration_plan": "plan",
            "run_migration_pev": "execute",
        }.get(tool_name)
        if task_id:
            _set_task(session["tasks"], task_id, "running")

        tool_result = await execute_tool(
            tool_name,
            args,
            session=session,
            accel_get=accel_get,
            accel_post=accel_post,
            build_plan=build_plan,
            session_token=session_token,
            llm=llm,
            llm_degraded=llm_degraded,
        )

        if tool_name == "run_migration_pev" and tool_result.get("status") == "starting":
            start_pev = True
            _set_task(session["tasks"], "execute", "running", "Pipeline starting")
            _set_task(session["tasks"], "validate", "pending")
        elif task_id:
            status = "failed" if tool_result.get("error") else "completed"
            _set_task(session["tasks"], task_id, status, tool_result.get("message", ""))

        _append_event(
            session,
            role="tool",
            content=json.dumps(tool_result, default=str)[:2000],
            kind="tool_result",
            subagent=subagent,
            meta={"tool": tool_name, "result": tool_result},
        )

        if tool_result.get("error"):
            err_reply = tool_result.get("message", tool_result["error"])
            result.reply = err_reply
            _append_event(session, role="assistant", content=err_reply, kind="message")
            session["subagent"] = None
            result.tasks = session["tasks"]
            return True, False

        if tool_name == "build_migration_plan":
            session.pop("migration_retry", None)
            session.pop("migration_replan", None)
            if _finalize_build_migration_plan(session, result, tool_result, prefix=prefix):
                result.tasks = session["tasks"]
                return True, False
            return False, start_pev

        if tool_name == "fetch_profile_discovery":
            continue

    return False, start_pev


async def process_user_message(
    session: dict[str, Any],
    user_message: str,
    *,
    llm: LLMProvider,
    llm_degraded: bool,
    llm_unconfigured: bool = False,
    accel_get: Callable[..., Awaitable[dict]],
    build_plan: Callable[..., Awaitable[dict]],
    session_token: str | None,
    accel_post: Callable[..., Awaitable[dict]] | None = None,
    max_iterations: int = 6,
) -> OrchestratorResult:
    if "tasks" not in session or not session["tasks"]:
        session["tasks"] = _init_tasks()

    _append_event(session, role="user", content=user_message, kind="message")
    session["_last_user_message"] = user_message

    apply_execution_mode_from_message(session, user_message)

    user_intent = _classify_user_intent(
        user_message, session, llm, llm_degraded=llm_degraded,
    )

    if user_intent == "out_of_scope" or is_out_of_scope_message(user_message):
        result = OrchestratorResult(tasks=session.get("tasks") or _init_tasks())
        result.reply = scope_refusal_reply(user_message)
        _append_event(session, role="assistant", content=result.reply, kind="message")
        session["subagent"] = None
        return result

    if user_intent == "general_chat":
        result = OrchestratorResult(tasks=session.get("tasks") or _init_tasks())
        if llm_unconfigured:
            from ado2gh.agents.llm_provider import NO_LLM_CONFIGURED_MESSAGE
            result.reply = NO_LLM_CONFIGURED_MESSAGE
        else:
            result.reply = _general_chat_reply(user_message, llm, session)
        _append_event(session, role="assistant", content=result.reply, kind="message")
        session["subagent"] = None
        return result

    if user_intent == "migration_info":
        result = OrchestratorResult(tasks=session.get("tasks") or _init_tasks())
        if llm_unconfigured:
            from ado2gh.agents.llm_provider import NO_LLM_CONFIGURED_MESSAGE
            result.reply = NO_LLM_CONFIGURED_MESSAGE
        else:
            result.reply = await _migration_info_reply(
                user_message,
                session,
                llm,
                accel_get,
                session_token,
                llm_degraded=llm_degraded,
            )
        result.pending_form = None
        _append_event(session, role="assistant", content=result.reply, kind="message")
        session["subagent"] = "validator"
        return result

    if _user_wants_retry_migration(user_message):
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session["migration_retry"] = True
        session.pop("migration_replan", None)
    elif _user_wants_replan_only(user_message):
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session["migration_replan"] = True
        session.pop("migration_retry", None)
    else:
        session.pop("migration_retry", None)
        session.pop("migration_replan", None)

    if (
        session.get("migration_plan")
        and not session.get("plan_approved")
        and _user_approves_plan(user_message)
        and not _user_requests_plan_changes(user_message)
    ):
        session["plan_approved"] = True

    extracted_phase = _extract_requested_phase(user_message)
    if extracted_phase:
        _, phase_err = _resolve_migration_phase(user_message, session)
        if phase_err:
            result = OrchestratorResult(tasks=session.get("tasks") or _init_tasks())
            result.reply = phase_err
            _append_event(session, role="assistant", content=phase_err, kind="message")
            session["subagent"] = None
            return result

    result = OrchestratorResult(tasks=session["tasks"])
    if llm_unconfigured:
        from ado2gh.agents.llm_provider import NO_LLM_CONFIGURED_MESSAGE

        result.reply = NO_LLM_CONFIGURED_MESSAGE
        _append_event(session, role="assistant", content=NO_LLM_CONFIGURED_MESSAGE, kind="message")
        session["subagent"] = None
        return result

    start_pev = False
    orchestration_prompt = user_message

    for _ in range(max_iterations):
        if llm_degraded:
            parsed = _stub_orchestrate(orchestration_prompt, session)
            tool_calls = parsed.get("tool_calls") or []
        else:
            tool_desc = json.dumps(INTERNAL_TOOLS, indent=2)
            context = {
                "profile_id": session.get("profile_id"),
                "user_role": session.get("user_role"),
                "user_display_name": session.get("user_display_name"),
                "has_discovery": bool(session.get("discovery_snapshot")),
                "has_plan": bool(session.get("migration_plan")),
                "plan_approved": bool(session.get("plan_approved")),
                "plan_blocked": bool((session.get("migration_plan") or {}).get("blocked")),
                "dry_run": session.get("dry_run", True),
                "live_approved": session.get("live_approval_status") == "approved",
                "requires_live_approval": session_requires_live_approval(session),
                "can_approve_live_execution": bool(
                    (session.get("permissions") or {}).get("can_approve_live_execution"),
                ),
                "available_phases": _available_migration_phases(session),
                "requested_phase": _extract_requested_phase(user_message),
            }
            prompt = (
                f"User message: {orchestration_prompt}\n\n"
                f"Session context: {json.dumps(context)}\n\n"
                f"Available tools:\n{tool_desc}"
            )
            raw = llm.complete(prompt, system=ORCHESTRATOR_SYSTEM)
            parsed = _parse_llm_json(raw)
            tool_calls = parsed.get("tool_calls") or []
            if tool_calls and not _migration_workflow_active(user_message):
                tool_calls = []

        thinking = parsed.get("thinking")
        if thinking:
            _append_event(
                session,
                role="assistant",
                content=str(thinking),
                kind="thinking",
                subagent=session.get("subagent"),
            )

        if not tool_calls:
            if not _migration_workflow_active(user_message):
                reply = parsed.get("reply") or _general_chat_reply(user_message, llm, session)
                _append_event(session, role="assistant", content=reply, kind="message")
                result.reply = reply
                break
            should_return, chain_start_pev = (False, False)
            if llm_degraded:
                should_return, chain_start_pev = await _auto_chain_migration_tools(
                    session,
                    user_message,
                    result,
                    accel_get=accel_get,
                    accel_post=accel_post,
                    build_plan=build_plan,
                    session_token=session_token,
                    llm=llm,
                    llm_degraded=llm_degraded,
                )
            if should_return:
                return result
            if chain_start_pev:
                if session.get("plan_approved"):
                    result.start_pev = True
                    plan = session.get("migration_plan") or {}
                    result.reply = plan.get("narrative") or "Starting migration pipeline…"
                    _append_event(session, role="assistant", content=result.reply, kind="message")
                    session["subagent"] = "executor"
                    result.tasks = session["tasks"]
                    return result
            if llm_degraded and _auto_migration_chain_step(session, user_message):
                orchestration_prompt = (
                    f"Original request: {user_message}\n"
                    "(auto-chained migration tools; continue if more steps are needed.)"
                )
                continue

            reply = parsed.get("reply")
            if not reply:
                reply = (
                    migration_ready_reply(session)
                    if _migration_workflow_active(user_message)
                    else _general_chat_reply(user_message, llm, session)
                )
            _append_event(session, role="assistant", content=reply, kind="message")
            result.reply = reply
            break

        if tool_calls and not _migration_workflow_active(user_message):
            reply = parsed.get("reply") or _general_chat_reply(user_message, llm, session)
            _append_event(session, role="assistant", content=reply, kind="message")
            result.reply = reply
            break

        resolved_form = False
        for call in tool_calls:
            tool_name = call.get("name", "")
            args = call.get("arguments") or {}
            tool_meta = INTERNAL_TOOLS.get(tool_name, {})
            subagent = tool_meta.get("subagent", "planner")
            session["subagent"] = subagent

            _append_event(
                session,
                role="assistant",
                content=tool_name,
                kind="tool_call",
                subagent=subagent,
                meta={"arguments": args},
            )

            task_id = {
                "fetch_profile_discovery": "discovery",
                "run_profile_scan": "discovery",
                "build_migration_plan": "plan",
                "run_migration_pev": "execute",
            }.get(tool_name)
            if task_id:
                _set_task(session["tasks"], task_id, "running")

            tool_result = await execute_tool(
                tool_name,
                args,
                session=session,
                accel_get=accel_get,
                accel_post=accel_post,
                build_plan=build_plan,
                session_token=session_token,
                llm=llm,
                llm_degraded=llm_degraded,
            )

            if tool_name == "run_migration_pev" and tool_result.get("status") == "starting":
                start_pev = True
                _set_task(session["tasks"], "execute", "running", "Pipeline starting")
                _set_task(session["tasks"], "validate", "pending")
                _append_event(
                    session,
                    role="system",
                    content="Starting executor and validator pipeline…",
                    kind="task_update",
                    subagent="executor",
                )
            elif task_id:
                status = "failed" if tool_result.get("error") else "completed"
                _set_task(session["tasks"], task_id, status, tool_result.get("message", ""))

            if (
                tool_name == "build_migration_plan"
                and _finalize_build_migration_plan(session, result, tool_result)
            ):
                result.tasks = session["tasks"]
                return result

            if tool_name == "fetch_migration_status":
                reply = tool_result.get("narrative") or "No migration status available."
                result.reply = reply
                _append_event(session, role="assistant", content=reply, kind="message")
                session["subagent"] = None
                result.tasks = session["tasks"]
                return result

            if tool_name == "request_user_input":
                if tool_result.get("auto_resolved"):
                    notice = _user_input_auto_resolve_message(tool_result, args, session)
                    _append_event(
                        session,
                        role="tool",
                        content=json.dumps(tool_result, default=str),
                        kind="tool_result",
                        subagent=subagent,
                        meta={"tool": tool_name, "result": tool_result},
                    )
                    phase = tool_result.get("phase") or session.get("plan_phase", "poc")
                    if phase:
                        session["plan_phase"] = phase
                    if session.get("discovery_snapshot") and not session.get("migration_plan"):
                        _set_task(session["tasks"], "plan", "running")
                        plan_result = await execute_tool(
                            "build_migration_plan",
                            {"phase": phase},
                            session=session,
                            accel_get=accel_get,
                            accel_post=accel_post,
                            build_plan=build_plan,
                            session_token=session_token,
                            llm=llm,
                            llm_degraded=llm_degraded,
                        )
                        plan_status = "failed" if plan_result.get("error") else "completed"
                        _set_task(
                            session["tasks"],
                            "plan",
                            plan_status,
                            plan_result.get("message", ""),
                        )
                        if _finalize_build_migration_plan(
                            session, result, plan_result, prefix=f"{notice}\n\n",
                        ):
                            result.tasks = session["tasks"]
                            return result
                    result.reply = notice
                    _append_event(session, role="assistant", content=notice, kind="message")
                    resolved_form = True
                    continue
                result.pending_form = session.get("pending_form")
                result.reply = (
                    parsed.get("reply")
                    or args.get("prompt")
                    or args.get("description")
                    or "Please complete the form below to continue."
                )
                _append_event(
                    session,
                    role="assistant",
                    content=result.reply,
                    kind="message",
                )
                session["subagent"] = None
                return result

            _append_event(
                session,
                role="tool",
                content=json.dumps(tool_result, default=str)[:2000],
                kind="tool_result",
                subagent=subagent,
                meta={"tool": tool_name, "result": tool_result},
            )

            if tool_result.get("error"):
                err_reply = tool_result.get("message", tool_result["error"])
                _append_event(session, role="assistant", content=err_reply, kind="message")
                result.reply = err_reply
                session["subagent"] = None
                return result

        if resolved_form:
            orchestration_prompt = (
                f"{user_message}\n(form auto-resolved; continue migration workflow)"
            )
            continue

        should_return, chain_start_pev = await _auto_chain_migration_tools(
            session,
            user_message,
            result,
            accel_get=accel_get,
            accel_post=accel_post,
            build_plan=build_plan,
            session_token=session_token,
            llm=llm,
            llm_degraded=llm_degraded,
        )
        if should_return:
            return result
        if chain_start_pev and session.get("plan_approved"):
            result.start_pev = True
            plan = session.get("migration_plan") or {}
            result.reply = (
                plan.get("narrative")
                or parsed.get("reply")
                or "Starting migration pipeline…"
            )
            _append_event(session, role="assistant", content=result.reply, kind="message")
            session["subagent"] = "executor"
            result.tasks = session["tasks"]
            return result

        if start_pev:
            if session.get("plan_approved"):
                result.start_pev = True
                plan = session.get("migration_plan") or {}
                result.reply = (
                    plan.get("narrative")
                    or parsed.get("reply")
                    or "Starting migration pipeline…"
                )
                _append_event(session, role="assistant", content=result.reply, kind="message")
                session["subagent"] = "executor"
                return result
            start_pev = False

        orchestration_prompt = (
            f"Original request: {user_message}\n"
            f"Completed tools: {[c.get('name') for c in tool_calls]}. "
            "Continue the migration workflow if discovery, plan, or execute is still needed."
        )
        if parsed.get("reply"):
            _append_event(session, role="assistant", content=parsed["reply"], kind="progress")

        if (
            session.get("migration_plan")
            and not _wants_migration_execute(user_message)
            and not session.get("migration_retry")
            and not _migration_status_intent(user_message)
        ):
            result.reply = parsed.get("reply") or migration_ready_reply(session)
            _append_event(session, role="assistant", content=result.reply, kind="message")
            session["subagent"] = None
            return result

    session["subagent"] = None
    result.tasks = session["tasks"]
    if not result.reply:
        result.reply = _orchestrator_fallback_reply(session)
        _append_event(session, role="assistant", content=result.reply, kind="message")
    return result
