"""Tool-driven PEV orchestrator — planner / executor / validator via guarded tools."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

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
3. run_migration_pev requires an existing migration_plan AND plan_approved=true (operator confirmed the plan).
4. After build_migration_plan succeeds, STOP and wait for operator confirmation — do NOT call run_migration_pev in the same turn.
5. When the operator asks for live execution (e.g. "no dry run", "migrate live"), set dry_run false before planning if they are admin/approver; operators need approval.
6. NEVER call run_migration_pev for a live session when session.requires_live_approval is true (operators only — admins and approvers may run live directly).
7. Use request_user_input when phase, profile, or confirmation is missing — only once per missing field; if phase was auto-resolved, call build_migration_plan next (do not call request_user_input again for phase).
8. For general questions, reply without tools.
9. Chain discovery → plan only until the operator confirms the plan; then run_migration_pev when they approve execution.
10. run_migration_pev starts the executor+validator pipeline; the LLM reviews each subagent phase and may retry up to 3 times.

Respond with JSON only:
{
  "thinking": "optional reasoning (shown when model supports thinking)",
  "tool_calls": [{"name": "tool_name", "arguments": {}}],
  "reply": "user-facing message when no more tools needed this turn"
}
"""

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


def _migration_intent(user_message: str) -> bool:
    msg = user_message.lower()
    return any(
        w in msg
        for w in (
            "migrate", "migration", "plan phase", "dry-run", "dry run",
            "execute", "pev", "discovery", "inventory", "service connection",
            "pipeline inventory", "scan", "retry", "try again", "deleted",
        )
    )


def _user_wants_retry_migration(user_message: str) -> bool:
    msg = user_message.lower()
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


def _phase_from_message(user_message: str, session: dict[str, Any]) -> str:
    phase = session.get("plan_phase", "poc")
    msg = user_message.lower()
    for p in ("poc", "pilot", "wave1", "wave2", "wave3"):
        if p in msg:
            return p
    return phase


def _wants_migration_execute(user_message: str) -> bool:
    msg = user_message.lower()
    if parse_execution_mode_from_message(user_message) is False and any(
        w in msg for w in ("migrate", "migration", "execute", "run", "start", "go")
    ):
        return True
    return any(w in msg for w in ("run", "execute", "start", "go", "yes", "dry-run", "dry run", "migrate"))


def _migration_tool_calls(session: dict[str, Any], user_message: str) -> list[dict[str, Any]]:
    """Deterministic discovery → plan → execute chain (one tool per stub iteration)."""
    snap = session.get("discovery_snapshot") or {}
    inventory_count = int(snap.get("pipeline_inventory_count") or 0)
    retry = bool(session.get("migration_retry"))
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
        return [{"name": "run_migration_pev", "arguments": {}}]
    return []


def _auto_migration_chain_step(session: dict[str, Any], user_message: str) -> dict[str, Any] | None:
    """Next migration tool to run automatically after scan/discovery without waiting for the LLM."""
    if _discovery_needs_fetch(session):
        return {"name": "fetch_profile_discovery", "arguments": {}}
    snap = session.get("discovery_snapshot") or {}
    if not snap:
        return None
    retry = bool(session.get("migration_retry"))
    if not session.get("migration_plan") or retry:
        if session.get("migration_plan") and retry:
            session.pop("migration_plan", None)
            session["plan_approved"] = False
        phase = _phase_from_message(user_message, session)
        return {"name": "build_migration_plan", "arguments": {"phase": phase}}
    return None


def _retry_chain_prefix(session: dict[str, Any]) -> str:
    if not session.get("migration_retry"):
        return ""
    return (
        "**Retry:** rescanned discovery and rebuilt the migration plan "
        "after the target repo was removed.\n\n"
    )


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
        "I've prepared a migration plan. **Please review it** — is it correct, "
        "or do you need changes?\n\n"
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
    if _migration_intent(user_message):
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
) -> dict[str, Any]:
    if not session.get("discovery_snapshot"):
        return {
            "error": "discovery_required",
            "message": "Call fetch_profile_discovery before build_migration_plan.",
        }
    phase = arguments.get("phase") or session.get("plan_phase", "poc")
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
    if name == "fetch_profile_discovery":
        return await _tool_fetch_discovery(session, accel_get, session_token)
    if name == "run_profile_scan":
        if not accel_post:
            return {"error": "scan_unavailable", "message": "Profile scan is not available."}
        return await _tool_run_profile_scan(session, accel_post, session_token)
    if name == "build_migration_plan":
        return await _tool_build_plan(
            session, arguments, build_plan, session_token, llm, llm_degraded=llm_degraded,
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

    apply_execution_mode_from_message(session, user_message)

    if _user_wants_retry_migration(user_message):
        session.pop("migration_plan", None)
        session["plan_approved"] = False
        session["migration_retry"] = True
    else:
        session.pop("migration_retry", None)

    if (
        session.get("migration_plan")
        and not session.get("plan_approved")
        and _user_approves_plan(user_message)
        and not _user_requests_plan_changes(user_message)
    ):
        session["plan_approved"] = True

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
            }
            prompt = (
                f"User message: {orchestration_prompt}\n\n"
                f"Session context: {json.dumps(context)}\n\n"
                f"Available tools:\n{tool_desc}"
            )
            raw = llm.complete(prompt, system=ORCHESTRATOR_SYSTEM)
            parsed = _parse_llm_json(raw)
            tool_calls = parsed.get("tool_calls") or []
            if not tool_calls and _migration_intent(user_message):
                stub = _stub_orchestrate(orchestration_prompt, session)
                if stub.get("tool_calls"):
                    parsed = stub
                    tool_calls = stub["tool_calls"]

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
                result.start_pev = True
                plan = session.get("migration_plan") or {}
                result.reply = plan.get("narrative") or "Starting migration pipeline…"
                _append_event(session, role="assistant", content=result.reply, kind="message")
                session["subagent"] = "executor"
                result.tasks = session["tasks"]
                return result
            if _auto_migration_chain_step(session, user_message):
                orchestration_prompt = (
                    f"Original request: {user_message}\n"
                    "(auto-chained migration tools; continue if more steps are needed.)"
                )
                continue

            reply = parsed.get("reply") or migration_ready_reply(session)
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
        if chain_start_pev:
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
