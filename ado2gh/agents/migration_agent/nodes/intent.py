"""intent.py module."""
from __future__ import annotations

import json
from typing import Any

from ado2gh.agents.migration_agent.constants import NO_LLM_CONFIGURED_MESSAGE
from ado2gh.agents.migration_agent.nodes._common import _transition_session
from ado2gh.agents.migration_agent.policies import is_out_of_scope_message, scope_refusal_reply
from ado2gh.agents.migration_agent.session.state import SessionState
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    publish_orchestrator_chat,
)


def _build_classification_prompt(user_message: str, session: dict[str, Any]) -> str:
    """Build the intent classification prompt."""
    discovery = session.get("discovery_snapshot")
    has_plan = bool(session.get("migration_plan"))
    has_discovery = bool(discovery and discovery.get("repos"))
    return json.dumps({
        "user_message": user_message,
        "has_discovery": has_discovery,
        "has_plan": has_plan,
        "session_status": session.get("status", "idle"),
    })


def _build_session_context(session: dict[str, Any]) -> str:
    """Build a context block from session state to inject into the LLM prompt."""
    ctx_parts: list[str] = []
    session_id = str(session.get("session_id") or "").strip()
    if session_id:
        ctx_parts.append(
            f"- session_id: {session_id} (isolated container — do not use migration state from other sessions)"
        )
    repo_id = session.get("plan_repository_id")
    if repo_id:
        ctx_parts.append(f"- repository_id: {repo_id} (already selected by operator in this session)")
    repo_ids = session.get("plan_repository_ids")
    if repo_ids:
        ctx_parts.append(f"- repository_ids: {', '.join(repo_ids)} (bulk migration — {len(repo_ids)} repos selected by operator)")
    dry_run = session.get("dry_run", True)
    ctx_parts.append(f"- dry_run: {dry_run}")
    if session.get("execution_mode_confirmed"):
        ctx_parts.append("- execution_mode_confirmed: true (operator chose dry-run or live)")
    elif repo_id or repo_ids:
        ctx_parts.append("- execution_mode_confirmed: false (must ask operator for dry-run vs live)")
    discovery = session.get("discovery_snapshot")
    if discovery:
        repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
        if repos:
            repo_names = [r.get("repo_name", r.get("name", str(r))) if isinstance(r, dict) else str(r) for r in repos[:10]]
            suffix = " …" if len(repos) > 10 else ""
            ctx_parts.append(f"- available_repos ({len(repos)} total, profile discovery): {', '.join(repo_names)}{suffix}")
    if session.get("migration_plan"):
        from ado2gh.agents.migration_agent.hitl.blockers import sanitize_plan_for_operator_view

        plan = sanitize_plan_for_operator_view(session["migration_plan"], session)
        work_items = plan.get("work_items", [])
        ctx_parts.append(
            f"- migration_plan: exists with {len(work_items)} work items, "
            f"{plan.get('repo_count', 0)} repo(s)"
            + (f", phase {plan['phase']}" if plan.get("phase") else "")
        )
        if work_items:
            ready = sum(1 for wi in work_items if wi.get("status") == "ready")
            skipped = sum(1 for wi in work_items if wi.get("status") == "skipped")
            ctx_parts.append(f"  - {ready} ready, {skipped} skipped work items (blockers omitted)")
    if session.get("plan_approved"):
        ctx_parts.append("- plan_approved: true")
    from ado2gh.agents.migration_agent.hitl.intake import (
        determine_intake_phase,
        intake_from_session,
        missing_intake_fields,
    )

    intake = intake_from_session(session)
    intake_phase = determine_intake_phase(session)
    if intake_phase:
        missing = [f.name for f in missing_intake_fields(intake, intake_phase)]
        if missing:
            ctx_parts.append(f"- intake_phase: {intake_phase.value}")
            ctx_parts.append(f"- intake_missing: {', '.join(missing)}")
    if not ctx_parts:
        return ""
    return "\n\n## Current session context\n" + "\n".join(ctx_parts)


def _begin_new_agent_migration(session: dict[str, Any], *, repository_id: str | None = None) -> None:
    """Reset monitor run linkage and stale plan state for a new migration attempt."""
    from ado2gh.agents.migration_agent.session.state import maybe_reset_for_migration_request

    if repository_id:
        maybe_reset_for_migration_request(session, repository_id, force=True)
    else:
        session.pop("run_id", None)
        session.pop("pev_execution_completed", None)
        session.pop("pev_execution_started", None)
        session.pop("pev_max_retries_exhausted", None)




async def _classify_user_intent(state: dict[str, Any]) -> dict[str, Any]:
    """Classify user intent via LLM + Pydantic (no heuristic text extraction)."""
    user_message = state.get("user_message", "")
    session = state.get("session") or {}
    _transition_session(session, SessionState.THINKING)
    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)

    if not user_message:
        return {"intent": "general_chat", "should_return": True}

    if is_out_of_scope_message(user_message):
        reply = scope_refusal_reply(user_message)
        publish_orchestrator_chat(session, reply)
        return {"intent": "general_chat", "reply": reply, "should_return": True}

    if llm_unconfigured or not llm:
        reply = NO_LLM_CONFIGURED_MESSAGE
        publish_orchestrator_chat(session, reply)
        return {"intent": "general_chat", "reply": reply, "should_return": True}

    from ado2gh.agents.migration_agent.hitl.intake_llm import analyze_operator_message

    analysis = await analyze_operator_message(llm, user_message, session)
    if analysis is not None:
        from ado2gh.agents.migration_agent.hitl.intake_guardrails import apply_analysis_guardrails

        analysis = apply_analysis_guardrails(analysis, user_message)
    if analysis is None:
        reply = (
            "I could not interpret that message. Please rephrase, or name the repository "
            "as Project/RepoName."
        )
        publish_orchestrator_chat(session, reply)
        return {"intent": "general_chat", "reply": reply, "should_return": True}

    if analysis.reasoning:
        _append_and_stream(
            session,
            role="system",
            content=analysis.reasoning,
            kind="thinking",
            subagent="orchestrator",
        )

    if analysis.is_cancellation:
        _append_event(session, role="system", content="Migration cancellation requested.", kind="message")
        return {"intent": "general_chat", "reply": "Migration cancellation requested.", "should_return": True}

    intent = analysis.intent.value
    _append_event(
        session,
        role="system",
        content=f"Intent classified: {intent}",
        kind="thinking",
        subagent="orchestrator",
    )

    result: dict[str, Any] = {
        "intent": intent,
        "iteration": state.get("iteration", 0) + 1,
        "operator_message_analysis": analysis.model_dump(),
    }
    sync_patch = analysis.intake_patch()
    if sync_patch.get("dry_run") is not None:
        session["dry_run"] = sync_patch["dry_run"]
        session["execution_mode_confirmed"] = True
    return result


async def classify_intent_node(state: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias — classification runs inside orchestrator_node."""
    return await _classify_user_intent(state)

