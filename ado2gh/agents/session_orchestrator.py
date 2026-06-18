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
7. Use request_user_input when phase, profile, or confirmation is missing.
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
        "description": "Load repos, phases, and risk scores from the active profile (required before planning).",
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
    """Append descriptive per-repo×scope tasks after the core PEV tasks."""
    tasks = session.get("tasks")
    if not tasks:
        tasks = _init_tasks()
    session["tasks"] = [t for t in tasks if t["id"] in PEV_TASK_IDS]
    for wi in work_items:
        if wi.get("status") == "skipped":
            continue
        status = wi.get("status", "pending")
        if status == "ready":
            status = "pending"
        session["tasks"].append({
            "id": wi["id"],
            "label": wi["label"],
            "subagent": {
                "migrate_repo": "executor",
                "convert_metadata": "executor",
                "manual_setup": "planner",
            }.get(wi.get("category", ""), "executor"),
            "category": wi.get("category"),
            "category_label": wi.get("category_label"),
            "status": status,
            "detail": wi.get("blocker") or wi.get("description", ""),
            "blocker": wi.get("blocker"),
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
            "execute", "pev", "discovery",
        )
    )


def _wants_migration_execute(user_message: str) -> bool:
    msg = user_message.lower()
    if parse_execution_mode_from_message(user_message) is False and any(
        w in msg for w in ("migrate", "migration", "execute", "run", "start", "go")
    ):
        return True
    return any(w in msg for w in ("run", "execute", "start", "go", "yes", "dry-run", "dry run", "migrate"))


def _migration_tool_calls(session: dict[str, Any], user_message: str) -> list[dict[str, Any]]:
    """Deterministic discovery → plan → execute chain (does not stop after the first tool)."""
    if not session.get("discovery_snapshot"):
        return [{"name": "fetch_profile_discovery", "arguments": {}}]
    if not session.get("migration_plan"):
        phase = session.get("plan_phase", "poc")
        msg = user_message.lower()
        for p in ("poc", "pilot", "wave1", "wave2", "wave3"):
            if p in msg:
                phase = p
                break
        return [{"name": "build_migration_plan", "arguments": {"phase": phase}}]
    if session.get("plan_approved") and _wants_migration_execute(user_message):
        return [{"name": "run_migration_pev", "arguments": {}}]
    return []


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


def _plan_confirmation_form(session: dict[str, Any]) -> dict[str, Any]:
    plan = session.get("migration_plan") or {}
    phase = plan.get("phase") or session.get("plan_phase", "poc")
    mode = "dry-run" if session.get("dry_run", True) else "live"
    repos = plan.get("repos") or []
    repo_lines = "\n".join(f"  - {r}" for r in repos[:15])
    if len(repos) > 15:
        repo_lines += f"\n  - … and {len(repos) - 15} more"
    steps = plan.get("pipeline_steps") or ["connect", "migrate", "validate"]
    narrative = (plan.get("narrative") or "").strip()
    description = (
        f"Phase: **{phase}** · Mode: **{mode}** · Repos: **{plan.get('repo_count', len(repos))}**\n"
        f"Pipeline: {' → '.join(steps)}\n"
    )
    if repo_lines:
        description += f"\n{repo_lines}\n"
    if narrative:
        description += f"\n{narrative}\n"
    description += (
        "\nConfirm the plan is correct, or describe changes in **Notes** "
        "(leave confirmation unchecked to request a replan)."
    )
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
                "type": "text",
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
            return {"auto_resolved": True, "phase": chosen}
        if len(phase_opts) == 1:
            session["plan_phase"] = phase_opts[0]
            return {"auto_resolved": True, "phase": phase_opts[0]}
        if "poc" in phase_opts:
            session["plan_phase"] = "poc"
            return {"auto_resolved": True, "phase": "poc"}
    form = {
        "form_id": arguments.get("form_id", reason),
        "title": arguments.get("title", "Additional information required"),
        "description": arguments.get(
            "description",
            "The agent needs this before continuing with planner/executor tools.",
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
    build_plan: Callable[..., Awaitable[dict]],
    session_token: str | None,
    llm: LLMProvider,
    llm_degraded: bool = False,
) -> dict[str, Any]:
    if name == "fetch_profile_discovery":
        return await _tool_fetch_discovery(session, accel_get, session_token)
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
    max_iterations: int = 6,
) -> OrchestratorResult:
    if "tasks" not in session or not session["tasks"]:
        session["tasks"] = _init_tasks()

    _append_event(session, role="user", content=user_message, kind="message")

    apply_execution_mode_from_message(session, user_message)

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
                and not tool_result.get("error")
                and not (session.get("migration_plan") or {}).get("blocked")
            ):
                form = _plan_confirmation_form(session)
                session["pending_form"] = form
                result.pending_form = form
                result.reply = _plan_confirmation_reply(session)
                _append_event(session, role="assistant", content=result.reply, kind="message")
                session["subagent"] = None
                return result

            if tool_name == "request_user_input":
                if tool_result.get("auto_resolved"):
                    _append_event(
                        session,
                        role="tool",
                        content=json.dumps(tool_result, default=str),
                        kind="tool_result",
                        subagent=subagent,
                        meta={"tool": tool_name, "result": tool_result},
                    )
                    resolved_form = True
                    continue
                result.pending_form = session.get("pending_form")
                result.reply = parsed.get("reply") or "Please complete the form below to continue."
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

        if session.get("migration_plan") and not _wants_migration_execute(user_message):
            result.reply = parsed.get("reply") or migration_ready_reply(session)
            _append_event(session, role="assistant", content=result.reply, kind="message")
            session["subagent"] = None
            return result

    session["subagent"] = None
    result.tasks = session["tasks"]
    if not result.reply and session.get("migration_plan"):
        result.reply = migration_ready_reply(session)
        _append_event(session, role="assistant", content=result.reply, kind="message")
    return result
