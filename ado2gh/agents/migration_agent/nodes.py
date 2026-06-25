"""LangGraph node implementations for the migration agent.

Each node function takes AgentState and returns a partial AgentState dict
to update. Nodes use LangChain ChatModel for LLM calls with native streaming.
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ado2gh.agents.migration_agent.constants import (
    LLM_TIMEOUT_SECONDS,
    MAX_ITERATIONS,
    MAX_PEV_RETRIES,
    NO_LLM_CONFIGURED_MESSAGE,
    PLANNER_MAX_RESEARCH_ROUNDS,
    PLANNER_MIN_RESEARCH_TOOL_CALLS,
    VALIDATOR_MAX_TOOL_ROUNDS,
    VALIDATOR_MIN_TOOL_CALLS_PIPELINES,
)
from ado2gh.agents.migration_agent.prompts import get_prompt
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    _append_status_message,
    _emit_tool_call,
    _emit_tool_result,
    _drain_message_queue,
    _has_queued_messages,
    _is_cancellation_request,
    _parse_llm_json,
    _queue_user_message,
    _reset_turn_status_budget,
    _safe_reply,
    publish_orchestrator_chat,
    find_discovery_repo,
    repo_not_found_clarification,
    validate_repo_against_discovery,
    canonical_repo_id,
    canonical_plan_repo_key,
    normalize_discovery_repo,
    normalize_repo_key,
)
from ado2gh.agents.migration_agent.policies import (
    is_out_of_scope_message,
    scope_refusal_reply,
)
from ado2gh.agents.migration_agent.context_window import build_context_with_cycle_summaries
from ado2gh.agents.migration_agent.session_state import (
    SessionState,
    SessionStateMachine,
    release_session_for_chat,
    set_session_idle,
)
from ado2gh.models import MigrationScope


def _is_start_execution_message(message: str) -> bool:
    """Return True when the operator is approving plan execution."""
    msg = message.lower()
    return any(
        phrase in msg
        for phrase in (
            "plan confirmed",
            "start migration",
            "start execution",
            "execute dry-run",
            "execute dry run",
            "run migration",
            "migrate live",
            "run it live",
        )
    )


def _is_pev_max_retries_exhausted(state: dict[str, Any], session: dict[str, Any]) -> bool:
    """True when validator exhausted PEV retries and operator escalation is required."""
    if session.get("pev_max_retries_exhausted"):
        return True
    validation = state.get("validation_result") or {}
    if validation.get("passed"):
        return False
    retry_count = int(state.get("pev_retry_count", session.get("pev_retry_count", 0)) or 0)
    if retry_count < MAX_PEV_RETRIES:
        return False
    feedback = state.get("validation_feedback")
    if isinstance(feedback, dict) and feedback.get("escalate"):
        return True
    # Validator clears retry feedback once retries are exhausted.
    if feedback is None and validation.get("failures"):
        return True
    return False


def _executor_result_for_repo_lock(
    session: dict[str, Any],
    *,
    repo_id: str,
    lock_holder_session_id: str | None,
    dry_run: bool,
    iteration: int,
    migration_queue: dict[str, Any] | None = None,
    failed: list[str] | None = None,
) -> dict[str, Any]:
    """Build executor return payload when repo lock cannot be acquired (queue index unchanged)."""
    from ado2gh.agents.migration_agent.operator_input import build_repo_lock_failure

    failure = build_repo_lock_failure(repo_id, lock_holder_session_id)
    _append_event(
        session,
        role="system",
        content=f"Executor: {failure['specific_failure']}",
        kind="thinking",
        subagent="executor",
    )
    skipped = [{
        "repo": repo_id,
        "reason": "locked",
        "details": [failure["specific_failure"]],
    }]
    if failed is not None:
        failed.append(repo_id)
        if migration_queue is not None:
            migration_queue["failed"] = failed
    result: dict[str, Any] = {
        "executor_result": {
            "per_repo_results": [],
            "failures": [failure],
            "skipped": skipped,
            "dry_run": dry_run,
            "current_repo_id": repo_id,
        },
        "iteration": iteration,
        "pending_clarification": None,
        "should_return": False,
    }
    if migration_queue is not None:
        result["migration_queue"] = migration_queue
    return result


def _load_operator_input_request(
    state: dict[str, Any],
    session: dict[str, Any],
) -> Any:
    """Resolve pending operator-input from session, graph state, or validation feedback."""
    from ado2gh.agents.migration_agent.operator_input import (
        assess_operator_input_needed,
        pending_operator_input,
    )
    from ado2gh.agents.migration_agent.operator_input_schema import OperatorInputRequest

    op_req = pending_operator_input(session)
    if op_req is None:
        raw = state.get("pending_operator_input")
        if raw:
            try:
                op_req = OperatorInputRequest.model_validate(raw)
            except Exception:
                op_req = None
    if op_req is None:
        pending_clarification = state.get("pending_clarification")
        if isinstance(pending_clarification, dict):
            payload = pending_clarification.get("payload") or {}
            if pending_clarification.get("message_type") == "operator_input_required":
                try:
                    op_req = OperatorInputRequest.model_validate(payload)
                except Exception:
                    op_req = None
    if op_req is None:
        validation = state.get("validation_result") or {}
        failures = validation.get("failures") or []
        feedback = state.get("validation_feedback")
        plan = state.get("migration_plan") or session.get("migration_plan") or {}
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except Exception:
                plan = {}
        if failures or feedback:
            op_req = assess_operator_input_needed(
                plan=plan if isinstance(plan, dict) else {},
                validation_feedback=feedback if isinstance(feedback, dict) else None,
                session=session,
            )
    return op_req


def _prepare_validation_escalation_session(session: dict[str, Any]) -> None:
    session.pop("start_execution", None)
    session["pev_max_retries_exhausted"] = True
    release_session_for_chat(session, outcome="failed")


async def _present_validation_failure_to_operator(
    state: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any] | None:
    """Publish operator chat + decision form when migration/validation cannot proceed."""
    from ado2gh.agents.migration_agent.operator_input import (
        failures_require_operator_escalation,
        fr036_operator_message,
        repo_lock_operator_message,
    )

    validation = state.get("validation_result") or {}
    if validation.get("passed"):
        return None
    failures = validation.get("failures") or []
    if not failures:
        return None

    force = failures_require_operator_escalation(failures)
    exhausted = _is_pev_max_retries_exhausted(state, session)
    if not force and not exhausted:
        return None

    executor_result = state.get("executor_result") or {}
    plan = state.get("migration_plan") or session.get("migration_plan") or {}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:
            plan = {}

    _prepare_validation_escalation_session(session)

    visible_failures = [
        f for f in failures
        if not _failure_is_benign(f, dry_run=bool(executor_result.get("dry_run", True)))
    ]
    failure_set = visible_failures or failures
    lock_msg = repo_lock_operator_message(failure_set)
    fr036_msg = fr036_operator_message(failure_set)

    op_req = _load_operator_input_request(state, session)
    if op_req is not None and not lock_msg and not fr036_msg:
        result = _present_operator_input(session, op_req)
        _append_and_stream(
            session,
            role="system",
            content="Orchestrator: validation failure escalated — operator decision required.",
            subagent="orchestrator",
        )
        result["start_execution"] = False
        result["chat_published"] = True
        result["pending_clarification"] = None
        return result

    if lock_msg:
        reply = publish_orchestrator_chat(session, lock_msg)
    elif fr036_msg:
        reply = publish_orchestrator_chat(session, fr036_msg)
    else:
        from ado2gh.agents.migration_agent.message_format import format_validation_failure_message

        reply = format_validation_failure_message(
            failure_set,
            validation_result={**validation, "failures": failure_set},
            executor_result=executor_result,
            session=session,
        )
        reply = publish_orchestrator_chat(session, reply)
        if op_req is not None:
            from ado2gh.agents.migration_agent.operator_input import (
                operator_input_to_form,
                store_operator_input,
            )

            store_operator_input(session, op_req)
            form = operator_input_to_form(op_req)
            session["pending_form"] = form
            return {
                "should_return": True,
                "reply": reply,
                "pending_form": form,
                "start_execution": False,
                "chat_published": True,
                "pending_clarification": None,
            }
    _append_and_stream(
        session,
        role="system",
        content="Orchestrator: validation failure escalated to operator chat.",
        subagent="orchestrator",
    )
    return {
        "should_return": True,
        "reply": reply,
        "start_execution": False,
        "chat_published": True,
        "pending_clarification": None,
    }


async def _present_pev_escalation_to_operator(
    state: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """Stop the PEV loop and explain validation failure to the operator."""
    result = await _present_validation_failure_to_operator(state, session)
    if result is not None:
        return result
    validation = state.get("validation_result") or {}
    failures = validation.get("failures") or []
    executor_result = state.get("executor_result") or {}
    from ado2gh.agents.migration_agent.message_format import format_validation_failure_message

    reply = format_validation_failure_message(
        failures,
        validation_result=validation,
        executor_result=executor_result,
        session=session,
    )
    session.pop("start_execution", None)
    session["pev_max_retries_exhausted"] = True
    release_session_for_chat(session, outcome="failed")
    reply = publish_orchestrator_chat(session, reply)
    return {
        "should_return": True,
        "reply": reply,
        "start_execution": False,
        "chat_published": True,
    }


def _present_operator_input(
    session: dict[str, Any],
    request: Any,
) -> dict[str, Any]:
    """Present a schema-driven operator-input form from planner/validator."""
    from ado2gh.agents.migration_agent.operator_input import (
        operator_input_to_form,
        store_operator_input,
    )

    store_operator_input(session, request)
    form = operator_input_to_form(request)
    reply = publish_orchestrator_chat(session, request.description)
    session["pending_form"] = form
    session.pop("plan_review_presented", None)
    return {
        "should_return": True,
        "reply": reply,
        "pending_form": form,
        "pending_operator_input": request.model_dump(),
    }


def _present_plan_confirmation(
    session: dict[str, Any],
    migration_plan: dict[str, Any],
) -> dict[str, Any]:
    """Present schema-driven plan review after the planner builds a plan."""
    from ado2gh.agents.migration_agent.forms import _plan_confirmation_reply, sanitize_form
    from ado2gh.agents.migration_agent.intake import build_plan_review_form

    session["migration_plan"] = migration_plan
    form = sanitize_form(build_plan_review_form(session))
    session["pending_form"] = form
    reply = publish_orchestrator_chat(session, _plan_confirmation_reply(session))
    return {
        "should_return": True,
        "reply": reply,
        "pending_form": form,
        "migration_plan": migration_plan,
        "start_pev": False,
    }


def _transition_session(session: dict[str, Any], target: SessionState) -> None:
    """Set session phase when an agent node starts."""
    from ado2gh.agents.migration_agent.session_state import set_session_phase

    set_session_phase(session, target)


# ─── Inter-agent messaging (T052) ─────────────────────────────────────

def _make_inter_agent_message(
    from_role: str,
    to_role: str,
    message_type: str,
    payload: dict[str, Any],
    correlation_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create an inter-agent message for AgentState.inter_agent_messages."""
    from datetime import datetime, timezone
    return {
        "message_id": f"{from_role}_{to_role}_{message_type}_{datetime.now(timezone.utc).isoformat()}",
        "from_role": from_role,
        "to_role": to_role,
        "message_type": message_type,
        "payload": payload,
        "correlation_ids": correlation_ids or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── PEV Cycle Summary (T054) ─────────────────────────────────────────

def _make_cycle_summary(
    cycle_number: int,
    executor_result: dict[str, Any],
    validation_result: dict[str, Any],
    next_action: str,
) -> dict[str, Any]:
    """Create a PevCycleSummary after each PEV cycle."""
    per_repo = executor_result.get("per_repo_results", [])
    failures = validation_result.get("failures", [])
    return {
        "cycle_number": cycle_number,
        "repos_processed": len(per_repo),
        "repos_succeeded": len(per_repo) - len(failures),
        "repos_failed": len(failures),
        "failures": failures[:10],  # Cap to prevent overflow
        "next_action": next_action,
    }


# ─── Streaming token collection ───────────────────────────────────────

async def _stream_llm_response(
    llm: Any,
    messages: list,
    state: dict[str, Any],
    *,
    subagent: str = "orchestrator",
    capabilities: Any = None,
) -> str:
    """Stream LLM response, collecting tokens and emitting live SSE events.

    Falls back to invoke() if astream() is unavailable or raises.
    Uses LangGraph's get_stream_writer() to push tokens in real-time.
    """
    import time
    from ado2gh.agents.metrics import get_metrics_collector

    metrics = get_metrics_collector()
    full_text = ""
    thinking_buffer = ""
    session = state.get("session") or {}
    supports_thinking = getattr(capabilities, "supports_thinking", False) if capabilities else False

    # Get LangGraph's stream writer for live token streaming
    writer = None
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
    except Exception:
        pass

    def emit(evt: dict[str, Any]) -> None:
        state.setdefault("_streaming_tokens", []).append(evt)
        # Only emit thinking events via stream writer — token events are raw LLM
        # JSON fragments that the frontend doesn't display
        if writer and evt.get("kind") != "token":
            writer(evt)

    start_time = time.time()
    try:
        async for chunk in llm.astream(messages):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            if token:
                full_text += token
                emit({"kind": "token", "content": token, "subagent": subagent})
            if supports_thinking and hasattr(chunk, "additional_kwargs"):
                thinking = chunk.additional_kwargs.get("thinking") or chunk.additional_kwargs.get("reasoning")
                if thinking:
                    thinking_buffer += str(thinking)
                    emit({"kind": "thinking", "content": thinking, "subagent": subagent})
            if supports_thinking and hasattr(chunk, "response_metadata"):
                meta = chunk.response_metadata or {}
                thinking = meta.get("thinking") or meta.get("reasoning_content")
                if thinking:
                    emit({"kind": "thinking", "content": thinking, "subagent": subagent})
    except (AttributeError, NotImplementedError, Exception):
        try:
            result = await llm.ainvoke(messages)
            full_text = result.content if hasattr(result, "content") else str(result)
            emit({"kind": "token", "content": full_text, "subagent": subagent})
        except Exception:
            full_text = ""
        else:
            if supports_thinking and hasattr(result, "additional_kwargs"):
                thinking = result.additional_kwargs.get("thinking") or result.additional_kwargs.get("reasoning")
                if thinking:
                    emit({"kind": "thinking", "content": thinking, "subagent": subagent})
    finally:
        duration = time.time() - start_time
        metrics.record_llm_call(duration)
        if thinking_buffer.strip() and session:
            from ado2gh.agents.migration_agent.utils import _append_event

            buffered = thinking_buffer.strip()
            recent = session.get("messages", [])[-3:]
            already = any(
                m.get("kind") == "thinking"
                and m.get("content") == buffered
                and m.get("subagent") == subagent
                for m in recent
            )
            if not already:
                _append_event(
                    session,
                    role="system",
                    content=buffered,
                    kind="thinking",
                    subagent=subagent,
                )

    return full_text


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
        from ado2gh.agents.migration_agent.blockers import sanitize_plan_for_operator_view

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
    from ado2gh.agents.migration_agent.intake import (
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
    from ado2gh.agents.migration_agent.session_state import maybe_reset_for_migration_request

    if repository_id:
        maybe_reset_for_migration_request(session, repository_id, force=True)
    else:
        session.pop("run_id", None)
        session.pop("pev_execution_completed", None)
        session.pop("pev_execution_started", None)
        session.pop("pev_max_retries_exhausted", None)


def _next_agent_run_label(session: dict[str, Any], repo_id: str, *, dry_run: bool) -> str:
    """Unique dashboard run name per agent migration attempt."""
    seq = int(session.get("agent_run_seq", 0)) + 1
    session["agent_run_seq"] = seq
    mode = "dry-run" if dry_run else "live"
    target = repo_id or "migration"
    return f"Agent: {target} ({mode} #{seq})"


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

    from ado2gh.agents.migration_agent.intake_llm import analyze_operator_message
    from ado2gh.agents.migration_agent.utils import load_discovery_snapshot

    accel_get = state.get("accel_get")
    if accel_get:
        await load_discovery_snapshot(
            session,
            accel_get,
            session_token=state.get("session_token"),
        )

    analysis = await analyze_operator_message(llm, user_message, session)
    if analysis is not None:
        from ado2gh.agents.migration_agent.intake_guardrails import apply_analysis_guardrails

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


# ─── Node: orchestrator ───────────────────────────────────────────────

async def orchestrator_node(state: dict[str, Any]) -> dict[str, Any]:
    """User-facing orchestrator: classify intent, handle messages, run tools, route to planner."""
    session = state.get("session") or {}
    _transition_session(session, SessionState.THINKING)
    # Planner handoff is one-shot; clear stale flag when a plan is already awaiting review.
    if (state.get("migration_plan") or session.get("migration_plan")) and not state.get("start_execution"):
        state = {**state, "start_pev": False}

    validation_result = state.get("validation_result") or {}
    if session.get("pev_max_retries_exhausted") or (
        validation_result.get("failures") and not validation_result.get("passed")
    ):
        escalated = await _present_pev_escalation_to_operator(state, session)
        return await _apply_orchestrator_tools(state, escalated)

    if not state.get("intent"):
        classified = await _classify_user_intent(state)
        state = {**state, **classified}
        if classified.get("should_return"):
            return classified

    result = await _orchestrator_node_impl(state)
    return await _apply_orchestrator_tools(state, result)


async def _orchestrator_node_impl(state: dict[str, Any]) -> dict[str, Any]:
    """Handle user messages based on classified intent.

    - general_chat: direct LLM response
    - migration_info: session context queries
    - migration_action: parameter collection, forms, route to Planner
    - form_submission: handle dynamic form submissions (e.g., rollback)
    - pending_clarification: handle clarification requests from Planner/Executor
    """
    user_message = state.get("user_message", "")
    session = state.get("session") or {}
    intent = state.get("intent", "general_chat")
    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    _reset_turn_status_budget(session)

    migration_plan = state.get("migration_plan") or session.get("migration_plan")

    # PEV completion callback — must run before start_execution re-trigger check
    validation_result = state.get("validation_result") or {}
    if validation_result.get("passed") and session.get("status") in (
        "planning", "executing", "validating", "thinking",
    ):
        executor_result = state.get("executor_result") or {}
        plan = migration_plan or {}
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except Exception:
                plan = {}
        repo_count = len(plan.get("repos", [])) if isinstance(plan, dict) else 0
        dry_run = executor_result.get("dry_run", session.get("dry_run", True))
        from ado2gh.agents.migration_agent.message_format import compose_migration_completion_message

        reply = await compose_migration_completion_message(
            state,
            session,
            validation_result=validation_result,
            executor_result=executor_result,
            migration_plan=plan if isinstance(plan, dict) else {},
        )
        completed_repo = (
            session.get("plan_repository_id")
            or (plan.get("repository_id") if isinstance(plan, dict) else None)
        )
        if completed_repo:
            session["last_completed_repository_id"] = completed_repo
        reply = publish_orchestrator_chat(session, reply)
        release_session_for_chat(session)
        _append_and_stream(
            session,
            role="system",
            content="Orchestrator: migration complete — summary sent to operator.",
            subagent="orchestrator",
        )
        return {"should_return": True, "reply": reply, "start_execution": False, "chat_published": True}

    # Validator exhausted retries — stop re-entering executor/planner loop
    if _is_pev_max_retries_exhausted(state, session):
        return await _present_pev_escalation_to_operator(state, session)

    # Operator-actionable validation failure (e.g. repo lock) — surface in chat immediately
    validation_result = state.get("validation_result") or {}
    if not validation_result.get("passed") and validation_result.get("failures"):
        escalated = await _present_validation_failure_to_operator(state, session)
        if escalated is not None:
            return escalated

    # Approved plan ready for execution — route directly to executor (once per approval)
    if (
        session.get("plan_approved")
        and migration_plan
        and _is_start_execution_message(user_message)
        and not session.get("pev_execution_completed")
        and not session.get("pev_max_retries_exhausted")
        and not validation_result.get("passed")
    ):
        if isinstance(migration_plan, str):
            try:
                migration_plan = json.loads(migration_plan)
            except Exception:
                migration_plan = {}
        migration_queue = state.get("migration_queue")
        if not migration_queue and isinstance(migration_plan, dict) and migration_plan.get("repos"):
            migration_queue = _build_migration_queue_from_plan(migration_plan)
            session["migration_queue"] = migration_queue
        _begin_new_agent_migration(session)
        session["start_execution"] = True
        session["pev_execution_started"] = True
        _transition_session(session, SessionState.EXECUTING)
        result: dict[str, Any] = {
            "start_execution": True,
            "migration_plan": migration_plan,
            "should_return": False,
        }
        if migration_queue is not None:
            result["migration_queue"] = migration_queue
        return result

    # Post-planner: operator input (blockers from plan or validator) before plan review
    if migration_plan and not session.get("plan_approved") and not state.get("start_execution"):
        if isinstance(migration_plan, str):
            try:
                migration_plan = json.loads(migration_plan)
            except Exception:
                migration_plan = None
        if isinstance(migration_plan, dict) and migration_plan.get("repos"):
            from ado2gh.agents.migration_agent.operator_input import (
                assess_operator_input_needed,
                pending_operator_input,
            )
            from ado2gh.agents.migration_agent.operator_input_schema import OperatorInputRequest

            session["migration_plan"] = migration_plan
            pending_form = state.get("pending_form") or session.get("pending_form")
            form_submission = state.get("form_submission")
            if pending_form and not form_submission:
                return {
                    "should_return": True,
                    "pending_form": pending_form,
                    "migration_plan": migration_plan,
                }

            op_req = pending_operator_input(session)
            if op_req is None:
                raw_pending = state.get("pending_operator_input")
                if raw_pending:
                    try:
                        op_req = OperatorInputRequest.model_validate(raw_pending)
                    except Exception:
                        op_req = None
            if op_req is None:
                validation_feedback = state.get("validation_feedback")
                if isinstance(validation_feedback, str):
                    try:
                        validation_feedback = json.loads(validation_feedback)
                    except Exception:
                        validation_feedback = None
                op_req = assess_operator_input_needed(
                    plan=migration_plan,
                    validation_feedback=validation_feedback if isinstance(validation_feedback, dict) else None,
                    session=session,
                    planner_request=migration_plan.get("operator_input_request"),
                )
            if op_req:
                result = _present_operator_input(session, op_req)
                result["migration_plan"] = migration_plan
                return result
            if not session.get("plan_review_presented"):
                session["plan_review_presented"] = True
                return _present_plan_confirmation(session, migration_plan)
            return {
                "should_return": True,
                "migration_plan": migration_plan,
            }

    # Handle pending_clarification from Planner/Executor
    pending_clarification = state.get("pending_clarification")
    if pending_clarification:
        msg_type = pending_clarification.get("message_type", "")
        payload = pending_clarification.get("payload", {})

        if msg_type == "operator_input_required":
            from ado2gh.agents.migration_agent.operator_input_schema import OperatorInputRequest

            try:
                op_req = OperatorInputRequest.model_validate(payload)
            except Exception:
                message = payload.get("reason", payload.get("message", "Operator input required."))
                publish_orchestrator_chat(session, message)
                return {
                    "should_return": True,
                    "reply": message,
                    "pending_clarification": None,
                }
            result = _present_operator_input(session, op_req)
            result["pending_clarification"] = None
            return result

        if msg_type == "repo_not_found":
            # Planner couldn't find the requested repo — present error + suggestions to user
            suggestions = payload.get("suggestions", [])
            error_msg = payload.get("message", "Repository not found.")
            # Emit the planner's error as a thinking event, not a chat message
            planner_thought = error_msg
            if suggestions:
                planner_thought += f" Did you mean: {', '.join(suggestions)}?"
            _append_event(
                session,
                role="system",
                content=planner_thought,
                kind="thinking",
                subagent="planner",
            )

            # Build form with suggestions in the description
            desc = "Enter the correct repository in Project/RepoName format."
            if suggestions:
                desc += f"\n\nSuggestions: {', '.join(suggestions[:5])}"

            from ado2gh.agents.migration_agent.intake import build_repo_error_form
            from ado2gh.agents.migration_agent.forms import sanitize_form

            form = sanitize_form(build_repo_error_form(desc, {"repo_suggestions": suggestions}))
            # Finalize immediately — wait for user to provide a new repo name
            # No reply — the form is the user-facing output, error is already a thinking event
            return {
                "should_return": True,
                "reply": None,
                "pending_form": form,
                "pending_clarification": None,
            }

        # Generic clarification — present to user and finalize
        message = payload.get("reason", payload.get("message", "Clarification needed."))
        publish_orchestrator_chat(session, message)
        return {
            "should_return": True,
            "reply": message,
            "pending_clarification": None,
        }

    # T101: Handle form submissions
    form_submission = state.get("form_submission")
    if form_submission:
        form_id = form_submission.get("form_id", "")
        values = form_submission.get("values", {})
        
        if form_id == "cancellation_options":
            action = values.get("action", "")
            if action == "rollback":
                # Execute rollback
                rollback_result = await _execute_rollback(state, session)
                reply = f"Rollback complete: {rollback_result.get('rollback_count', 0)} resources deleted, {rollback_result.get('rollback_failed', 0)} failed."
                publish_orchestrator_chat(session, reply)
                return {"should_return": True, "reply": reply}
            else:
                # Stop only
                reply = "Migration stopped. Resources remain in place."
                publish_orchestrator_chat(session, reply)
                return {"should_return": True, "reply": reply}
        
        # Unknown form — feed to LLM if available, otherwise acknowledge
        if llm and not llm_unconfigured:
            # Let the LLM process the form submission dynamically
            pass
        else:
            return {"should_return": True, "reply": "Form submitted."}

    # Queue message if PEV is active
    if session.get("status") in ("planning", "executing", "validating"):
        if _is_cancellation_request(user_message):
            # T076: Present rollback/stop options via dynamic form
            rollback_records = state.get("rollback_records", [])
            has_rollback = bool(rollback_records)
            form = {
                "form_id": "cancellation_options",
                "title": "Migration Cancellation",
                "description": "Choose how to handle the cancelled migration.",
                "fields": [
                    {
                        "name": "action",
                        "label": "Action",
                        "type": "select",
                        "required": True,
                        "options": [
                            {"value": "stop", "label": "Stop only — leave migrated resources in place"},
                            {"value": "rollback", "label": "Rollback — delete session-created resources", "disabled": not has_rollback},
                        ],
                    },
                ],
            }
            if has_rollback:
                form["fields"].append({
                    "name": "confirm_rollback",
                    "label": "Confirm rollback — this will delete GitHub resources created by this session",
                    "type": "checkbox",
                    "required": True,
                })
            session["status"] = "idle"
            _append_event(session, role="system", content="Migration cancelled by operator. Select rollback option.", kind="message")
            return {"should_return": True, "reply": "Migration cancelled. Please choose an action below.", "pending_form": form}
        _queue_user_message(session, user_message)
        return {"should_return": True, "reply": "Message queued — I'll process it after the current migration step completes."}

    # Active intake workflow — route by schema even when intent is general_chat
    # (e.g. operator replies "dry run" after selecting a repository).
    # Skip when planner already produced a plan awaiting operator review.
    from ado2gh.agents.migration_agent.intake import IntakePhase, determine_intake_phase

    if session.get("pev_max_retries_exhausted"):
        return {"should_return": True, "reply": None, "start_execution": False}

    if not (migration_plan and not session.get("plan_approved")):
        intake_phase = determine_intake_phase(session, intent=intent)
        if intake_phase is not None:
            routing_intent = "migration_action" if intake_phase == IntakePhase.PLANNING else intent
            intake_result = await _apply_intake_routing(
                state,
                session,
                user_message,
                intent=routing_intent,
            )
            if intake_result is not None:
                return intake_result

    if intent == "general_chat":
        return await _handle_general_chat(state, llm, llm_unconfigured, user_message, session)

    if intent == "migration_info":
        return await _handle_migration_info(state, llm, llm_unconfigured, user_message, session)

    if intent == "migration_action":
        return await _handle_migration_action(state, llm, llm_unconfigured, user_message, session)

    # Unmatched intent — let LLM handle if available, otherwise hardcoded fallback
    if llm and not llm_unconfigured:
        return await _handle_general_chat(state, llm, llm_unconfigured, user_message, session)
    return {"should_return": True, "reply": "I didn't understand that request."}


async def _handle_general_chat(
    state: dict[str, Any],
    llm: Any,
    llm_unconfigured: bool,
    user_message: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Handle general chat with direct LLM response."""
    if llm_unconfigured or not llm:
        reply = publish_orchestrator_chat(session, NO_LLM_CONFIGURED_MESSAGE)
        return {"should_return": True, "reply": reply}

    system_prompt = get_prompt("orchestrator") + _build_session_context(session)
    # T097: Apply context window trimming
    existing_messages = state.get("messages", [])
    cycle_summaries = state.get("cycle_summaries", [])
    max_budget = state.get("max_token_budget", 32000)
    
    # Build new messages with system prompt and user message
    new_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    
    # Combine with existing messages and trim
    all_messages = existing_messages + new_messages
    trimmed_messages = build_context_with_cycle_summaries(
        all_messages, cycle_summaries, max_budget
    )
    
    response_text = await _stream_llm_response(llm, trimmed_messages, state, subagent="orchestrator", capabilities=state.get("capabilities"))
    parsed = _parse_llm_json(response_text)
    thinking = parsed.get("thinking")
    if thinking:
        recent = session.get("messages", [])[-3:]
        already = any(
            m.get("kind") == "thinking"
            and m.get("content") == thinking
            and m.get("subagent") == "orchestrator"
            for m in recent
        )
        if not already:
            _append_event(session, role="system", content=thinking, kind="thinking", subagent="orchestrator")
            try:
                from langgraph.config import get_stream_writer
                w = get_stream_writer()
                if w:
                    w({"kind": "thinking", "content": thinking, "subagent": "orchestrator"})
            except Exception:
                pass

    tool_calls = parsed.get("tool_calls", [])
    if tool_calls:
        reply = parsed.get("reply")
        has_form_tool = any(tc.get("name") == "request_user_input" for tc in tool_calls)
        has_invoke_planner = any(
            tc.get("name") in ("invoke_planner", "invoke_bulk_planner") for tc in tool_calls
        )
        if reply and not has_form_tool and not has_invoke_planner:
            publish_orchestrator_chat(session, reply)
        elif reply and has_invoke_planner:
            _append_and_stream(session, role="system", content=reply, subagent="orchestrator")
        return {
            "tool_calls": tool_calls,
            "thinking": thinking,
            "should_return": False,
            "reply": reply if not has_form_tool and not has_invoke_planner else None,
        }

    reply = publish_orchestrator_chat(session, _safe_reply(parsed, response_text))
    return {"should_return": True, "reply": reply, "thinking": thinking}


async def _execute_rollback(
    state: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """T101: Execute rollback by deleting GitHub resources from rollback_records."""
    rollback_records = state.get("rollback_records", [])
    accel_post = state.get("accel_post")
    session_token = state.get("session_token")
    
    if not rollback_records:
        _append_event(session, role="system", content="Rollback: no resources to rollback.", kind="thinking", subagent="executor")
        return {"rollback_complete": True, "rollback_count": 0}
    
    _append_event(
        session,
        role="system",
        content=f"Rollback: starting deletion of {len(rollback_records)} resources…",
        kind="thinking",
        subagent="executor",
    )
    
    deleted_count = 0
    failed_count = 0
    
    for record in rollback_records:
        resource_type = record.get("resource_type", "")
        resource_name = record.get("resource_name", "")
        github_org = record.get("github_org", "")
        
        _append_event(
            session,
            role="system",
            content=f"Rollback: deleting {resource_type} {resource_name}…",
            kind="thinking",
            subagent="executor",
        )
        
        if not accel_post:
            _append_event(
                session,
                role="system",
                content=f"Rollback: accelerator unavailable for {resource_name}",
                kind="thinking",
                subagent="executor",
            )
            failed_count += 1
            continue
        
        try:
            # Call accelerator rollback endpoint
            await accel_post(
                "/v1/sessions/{session_id}/rollback",
                json={
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "github_org": github_org,
                },
                session_token=session_token,
            )
            deleted_count += 1
            _append_event(
                session,
                role="system",
                content=f"Rollback: deleted {resource_type} {resource_name}",
                kind="thinking",
                subagent="executor",
            )
        except Exception as e:
            failed_count += 1
            _append_event(
                session,
                role="system",
                content=f"Rollback: failed to delete {resource_name} - {str(e)}",
                kind="thinking",
                subagent="executor",
            )
    
    _append_event(
        session,
        role="system",
        content=f"Rollback: complete — {deleted_count} deleted, {failed_count} failed.",
        kind="thinking",
        subagent="executor",
    )
    
    return {
        "rollback_complete": True,
        "rollback_count": deleted_count,
        "rollback_failed": failed_count,
    }


async def _handle_migration_info(
    state: dict[str, Any],
    llm: Any,
    llm_unconfigured: bool,
    user_message: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Handle migration info queries using session context data."""
    # Use session context only — discovery data is loaded by the Planner, not here.
    info_text = ""
    discovery = session.get("discovery_snapshot")
    if discovery:
        repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
        info_text = f"Discovery data: {len(repos)} repos found."
    else:
        info_text = "No discovery data loaded yet. Start a migration to trigger discovery."
    if session.get("run_id"):
        info_text += f" Run ID: {session['run_id']}."

    if llm and not llm_unconfigured:
        system_prompt = get_prompt("orchestrator") + _build_session_context(session)
        # T097: Apply context window trimming
        existing_messages = state.get("messages", [])
        cycle_summaries = state.get("cycle_summaries", [])
        max_budget = state.get("max_token_budget", 32000)
        
        new_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{user_message}\n\nContext: {info_text}"),
        ]
        
        all_messages = existing_messages + new_messages
        trimmed_messages = build_context_with_cycle_summaries(
            all_messages, cycle_summaries, max_budget
        )
        
        response_text = await _stream_llm_response(llm, trimmed_messages, state, subagent="orchestrator", capabilities=state.get("capabilities"))
        parsed = _parse_llm_json(response_text)
        thinking = parsed.get("thinking")
        if thinking:
            _append_event(session, role="system", content=thinking, kind="thinking", subagent="orchestrator")
            try:
                from langgraph.config import get_stream_writer
                w = get_stream_writer()
                if w:
                    w({"kind": "thinking", "content": thinking, "subagent": "orchestrator"})
            except Exception:
                pass
        reply = _safe_reply(parsed, response_text)
    else:
        reply = info_text or "No migration data available."
        thinking = None

    reply = publish_orchestrator_chat(session, reply)
    return {"should_return": True, "reply": reply, "thinking": thinking}


async def _apply_intake_routing(
    state: dict[str, Any],
    session: dict[str, Any],
    user_message: str,
    *,
    intent: str = "migration_action",
) -> dict[str, Any] | None:
    """Schema extraction + routing — ask only for missing intake fields."""
    from ado2gh.agents.migration_agent.intake import analysis_from_state, resolve_intake_routing

    routing = await resolve_intake_routing(
        state,
        session,
        intent=intent,
        user_message=user_message,
        analysis=analysis_from_state(state),
    )
    action = routing.get("action")
    if action == "request_form":
        missing = routing.get("missing_fields") or []
        return {
            "tool_calls": [{
                "name": "request_user_input",
                "arguments": routing["form"],
            }],
            "thinking": f"Missing intake fields: {', '.join(missing)}",
            "should_return": False,
            "reply": None,
        }
    if action == "invoke_planner":
        repo_id = routing.get("repository_id", "")
        dry_run = routing.get("dry_run", True)
        from ado2gh.agents.migration_agent.intake_guardrails import repository_confirmed_for_turn

        analysis = analysis_from_state(state)
        if not repository_confirmed_for_turn(repo_id, analysis, session=session):
            missing_form = await resolve_intake_routing(
                state,
                session,
                intent=intent,
                user_message=user_message,
                analysis=analysis,
            )
            if missing_form.get("action") == "request_form":
                return {
                    "tool_calls": [{
                        "name": "request_user_input",
                        "arguments": missing_form["form"],
                    }],
                    "thinking": "Repository must be named by the operator before planning.",
                    "should_return": False,
                    "reply": None,
                }
            return None
        session["plan_repository_id"] = repo_id
        session["dry_run"] = dry_run
        session["execution_mode_confirmed"] = True
        mode = "dry-run" if dry_run else "live"
        synth_reply = f"Proceeding to build the migration plan for '{repo_id}' in {mode} mode."
        _append_and_stream(session, role="system", content=synth_reply, subagent="orchestrator")
        return {
            "tool_calls": [{
                "name": "invoke_planner",
                "arguments": {"repository_id": repo_id, "dry_run": dry_run},
            }],
            "thinking": routing.get("thinking"),
            "should_return": False,
            "reply": None,
        }
    return None


async def _handle_migration_action(
    state: dict[str, Any],
    llm: Any,
    llm_unconfigured: bool,
    user_message: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Handle migration action requests — collect params, build forms, route to Planner."""
    # Information schema: extract from conversation and route to missing fields only.
    intake_result = await _apply_intake_routing(state, session, user_message)
    if intake_result is not None:
        return intake_result

    # Discovery data is loaded by the Planner, not the orchestrator.
    # The orchestrator only collects parameters and interfaces with the user.

    if llm and not llm_unconfigured:
        system_prompt = get_prompt("orchestrator") + _build_session_context(session)
        # T097: Apply context window trimming
        existing_messages = state.get("messages", [])
        cycle_summaries = state.get("cycle_summaries", [])
        max_budget = state.get("max_token_budget", 32000)
        
        new_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        
        all_messages = existing_messages + new_messages
        trimmed_messages = build_context_with_cycle_summaries(
            all_messages, cycle_summaries, max_budget
        )
        
        response_text = await _stream_llm_response(llm, trimmed_messages, state, subagent="orchestrator", capabilities=state.get("capabilities"))
        parsed = _parse_llm_json(response_text)

        # Check for tool calls in LLM response
        tool_calls = parsed.get("tool_calls", [])

        # If LLM produced no tool calls and no reply, try to extract a reply from the raw text
        if not tool_calls and not parsed.get("reply"):
            # LLM output was unparseable — let _safe_reply handle it
            pass
        thinking = parsed.get("thinking")
        if thinking:
            try:
                from langgraph.config import get_stream_writer
                w = get_stream_writer()
                if w:
                    w({"kind": "thinking", "content": thinking, "subagent": "orchestrator"})
            except Exception:
                pass

        if tool_calls:
            reply = parsed.get("reply")
            # Suppress reply text when presenting a form — the form is the response
            has_form_tool = any(tc.get("name") == "request_user_input" for tc in tool_calls)
            # When invoking planner, emit reply as a thought not a chat message
            has_invoke_planner = any(tc.get("name") in ("invoke_planner", "invoke_bulk_planner") for tc in tool_calls)
            if reply and not has_form_tool and not has_invoke_planner:
                publish_orchestrator_chat(session, reply)
            elif reply and has_invoke_planner:
                actual_repo = ""
                for tc in tool_calls:
                    if tc.get("name") in ("invoke_planner", "invoke_bulk_planner"):
                        args = tc.get("arguments") or {}
                        actual_repo = str(
                            args.get("repository_id")
                            or (args.get("repository_ids") or [""])[0]
                            or ""
                        ).strip()
                        break
                if not actual_repo:
                    actual_repos = session.get("plan_repository_ids") or []
                    actual_repo = session.get("plan_repository_id", "")
                    if not actual_repo and actual_repos:
                        actual_repo = ", ".join(actual_repos)
                actual_dry_run = session.get("dry_run", True)
                if actual_repo:
                    mode = "dry-run" if actual_dry_run else "live"
                    reply = f"Proceeding to build the migration plan for '{actual_repo}' in {mode} mode."
                _append_and_stream(session, role="system", content=reply, subagent="orchestrator")
            return {
                "tool_calls": tool_calls,
                "thinking": thinking,
                "should_return": False,
                "reply": reply if not has_form_tool and not has_invoke_planner else None,
            }

        # Check for reply
        reply = _safe_reply(parsed, response_text)

        # Fallback: if session has plan_repository_ids (bulk) but LLM didn't
        # generate tool_calls, synthesize invoke_bulk_planner automatically.
        repo_ids = session.get("plan_repository_ids") or []
        if repo_ids and not session.get("migration_plan"):
            intake_result = await _apply_intake_routing(state, session, user_message)
            if intake_result is not None:
                return intake_result
            actual_dry_run = session.get("dry_run", True)
            mode = "dry-run" if actual_dry_run else "live"
            synth_reply = f"Proceeding to build the migration plan for {len(repo_ids)} repo(s) in {mode} mode."
            _append_and_stream(session, role="system", content=synth_reply, subagent="orchestrator")
            return {
                "tool_calls": [{
                    "name": "invoke_bulk_planner",
                    "arguments": {
                        "repository_ids": repo_ids,
                        "dry_run": actual_dry_run,
                    },
                }],
                "thinking": thinking,
                "should_return": False,
                "reply": None,
            }

        if reply:
            publish_orchestrator_chat(session, reply)
            return {"should_return": True, "reply": reply}

    # No LLM — schema-driven intake routing
    if not session.get("plan_repository_id") and not session.get("plan_repository_ids") and not session.get("migration_plan"):
        intake_result = await _apply_intake_routing(state, session, user_message)
        if intake_result is not None:
            return intake_result
    _append_event(
        session,
        role="assistant",
        content="I'll help you with that migration. Let me collect the required information.",
        kind="message",
    )
    return {"should_return": True, "reply": "Migration action received. Configure an LLM model for full agent capabilities."}


# ─── Node: planner ────────────────────────────────────────────────────

_PLANNER_RESEARCH_TOOLS = frozenset({
    "get_current_profile",
    "ado_api_query",
    "github_api_query",
    "call_accelerator",
})


async def _execute_planner_tool_call(
    tc: dict[str, Any],
    session: dict[str, Any],
    accel_get: Any,
    session_token: str | None,
) -> dict[str, Any]:
    """Run a single read-only planner tool call."""
    tool_name = str(tc.get("name", ""))
    args = tc.get("arguments") or {}
    if not isinstance(args, dict):
        args = {}

    if tool_name == "get_current_profile":
        from ado2gh.agents.migration_agent.tools.shared_tools import fetch_current_profile

        try:
            result = await fetch_current_profile(
                accel_get,
                session_token=session_token,
                session_getter=lambda: session,
            )
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    if tool_name == "ado_api_query" and accel_get:
        try:
            endpoint = str(args.get("endpoint", "")).lstrip("/")
            result = await accel_get(f"/v1/ado/{endpoint}", session_token=session_token)
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    if tool_name == "github_api_query" and accel_get:
        try:
            endpoint = str(args.get("endpoint", "")).lstrip("/")
            result = await accel_get(f"/v1/github/{endpoint}", session_token=session_token)
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    if tool_name == "call_accelerator" and accel_get:
        ep = str(args.get("endpoint", "")).lstrip("/")
        if not ep:
            return {"tool": tool_name, "arguments": args, "error": "endpoint_required"}
        try:
            result = await accel_get(f"/{ep}", session_token=session_token)
            if ep.endswith("/discovery") or "/discovery" in ep:
                session["discovery_snapshot"] = result
            return {"tool": tool_name, "arguments": args, "result": result}
        except Exception as exc:
            return {"tool": tool_name, "arguments": args, "error": str(exc)}

    return {"tool": tool_name, "arguments": args, "error": "tool_unavailable"}


async def _gather_planner_baseline_context(
    session: dict[str, Any],
    repos: list[dict[str, Any]],
    accel_get: Any,
    session_token: str | None,
) -> list[dict[str, Any]]:
    """Deterministic pre-LLM probes so planning always has source/target signals."""
    from ado2gh.agents.migration_agent.pipeline_plan import repo_config_from_discovery

    findings: list[dict[str, Any]] = []
    discovery = session.get("discovery_snapshot") or {}
    discovery_repos = discovery.get("repos") or [] if isinstance(discovery, dict) else []
    pipeline_counts: dict[str, int] = {}
    if isinstance(discovery, dict):
        for pipe in discovery.get("pipelines") or []:
            if not isinstance(pipe, dict):
                continue
            repo_name = str(pipe.get("repo_name") or pipe.get("repository") or "")
            if repo_name:
                pipeline_counts[repo_name] = pipeline_counts.get(repo_name, 0) + 1

    for repo in repos:
        if not isinstance(repo, dict):
            continue
        repo_key = canonical_repo_id(repo)
        entry: dict[str, Any] = {"repo": repo_key}
        try:
            cfg = repo_config_from_discovery(repo, session)
            entry["github_org"] = cfg.gh_org
            entry["github_repo"] = cfg.gh_repo
            if not str(cfg.gh_org or "").strip():
                entry["github_org_missing"] = True
        except Exception as exc:
            entry["config_error"] = str(exc)
            findings.append(entry)
            continue

        entry["discovery_pipeline_count"] = pipeline_counts.get(
            repo.get("repo_name") or repo.get("name") or "",
            int(repo.get("pipeline_count") or 0),
        )
        entry["repo_feature_detection"] = (
            (session.get("repo_feature_detection") or {}).get(repo_key) or {}
        )

        if accel_get and entry.get("github_org") and entry.get("github_repo"):
            from ado2gh.agents.migration_agent.operator_input import parse_github_target_probe

            try:
                gh_path = f"/v1/github/repos/{entry['github_org']}/{entry['github_repo']}"
                gh_resp = await accel_get(gh_path, session_token=session_token)
                entry["github_target"] = parse_github_target_probe(
                    gh_resp if isinstance(gh_resp, dict) else None,
                )
            except Exception as exc:
                entry["github_target"] = parse_github_target_probe(error=exc)

        project = repo.get("project") or (repo_key.split("/", 1)[0] if "/" in repo_key else "")
        repo_name = repo.get("repo_name") or repo.get("name") or ""
        from ado2gh.agents.migration_agent.utils import find_discovery_repo

        disc_match = find_discovery_repo(repo_key, discovery_repos) if discovery_repos else None
        if accel_get and project and repo_name:
            try:
                ado_resp = await accel_get(
                    f"/v1/ado/projects/{project}/repos/{repo_name}",
                    session_token=session_token,
                )
                entry["ado_repo"] = {
                    "id": (ado_resp or {}).get("id") if isinstance(ado_resp, dict) else None,
                    "default_branch": (ado_resp or {}).get("defaultBranch") if isinstance(ado_resp, dict) else None,
                }
                if not entry["ado_repo"].get("id") and disc_match:
                    entry["ado_repo"] = {
                        "id": disc_match.get("id") or disc_match.get("repo_id") or repo_key,
                        "default_branch": disc_match.get("default_branch"),
                        "source": "discovery_snapshot",
                    }
            except Exception as exc:
                if disc_match:
                    entry["ado_repo"] = {
                        "id": disc_match.get("id") or disc_match.get("repo_id") or repo_key,
                        "default_branch": disc_match.get("default_branch"),
                        "source": "discovery_snapshot",
                    }
                else:
                    entry["ado_repo"] = {"error": str(exc)}

        findings.append(entry)

    session["planner_baseline_context"] = findings
    return findings


def _planner_text_indicates_blocker(parsed: dict[str, Any]) -> bool:
    text = " ".join(
        str(parsed.get(key, ""))
        for key in ("thinking", "risk_summary", "reply")
    ).lower()
    hints = (
        "operator",
        "confirmation",
        "missing",
        "not found",
        "blocker",
        "cannot proceed",
        "critical",
        "verify",
    )
    return any(hint in text for hint in hints)


def _planner_append_thinking(
    session: dict[str, Any],
    content: Any,
    seen: set[str],
) -> None:
    text = str(content or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    _append_and_stream(
        session,
        role="system",
        content=text,
        kind="thinking",
        subagent="planner",
    )


def _planner_operator_input_return(
    session: dict[str, Any],
    plan: dict[str, Any],
    migration_queue: dict[str, Any],
    iteration: int,
    op_req: Any,
) -> dict[str, Any]:
    from ado2gh.agents.migration_agent.operator_input import store_operator_input

    store_operator_input(session, op_req)
    _append_and_stream(
        session,
        role="system",
        content="Planner: operator decision required — handing to orchestrator.",
        subagent="planner",
    )
    payload = op_req.model_dump() if hasattr(op_req, "model_dump") else op_req
    return {
        "migration_plan": plan,
        "migration_queue": migration_queue,
        "iteration": iteration,
        "pending_clarification": {
            "message_type": "operator_input_required",
            "payload": payload,
        },
        "pending_operator_input": payload,
        "planner_next": "orchestrator",
        "should_return": False,
        "start_pev": False,
    }


async def _run_planner_research_loop(
    state: dict[str, Any],
    session: dict[str, Any],
    llm: Any,
    conversation: list[Any],
    *,
    capabilities: Any = None,
    accel_get: Any = None,
    session_token: str | None = None,
    validation_feedback: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Multi-turn planner loop: tool research → final plan JSON."""
    research_tool_calls = 0
    last_parsed: dict[str, Any] = {}
    replan = bool(validation_feedback)
    seen_thinking: set[str] = set()

    for round_idx in range(PLANNER_MAX_RESEARCH_ROUNDS):
        response_text = await _stream_llm_response(
            llm,
            conversation,
            state,
            subagent="planner",
            capabilities=capabilities,
        )
        last_parsed = _parse_llm_json(response_text)
        _planner_append_thinking(session, last_parsed.get("thinking"), seen_thinking)

        if last_parsed.get("operator_input_request"):
            return last_parsed

        tool_calls = last_parsed.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        if last_parsed.get("repos"):
            research_ok = (
                replan
                or last_parsed.get("research_complete")
                or research_tool_calls >= PLANNER_MIN_RESEARCH_TOOL_CALLS
            )
            if research_ok:
                return last_parsed
            conversation.append(AIMessage(content=response_text))
            conversation.append(
                HumanMessage(
                    content=(
                        f"Complete at least {PLANNER_MIN_RESEARCH_TOOL_CALLS} read-only API "
                        "probes (ADO pipelines, GitHub target repo, dependencies) using "
                        "tool_calls before finalizing. Then set research_complete: true "
                        "with the plan JSON."
                    )
                )
            )
            continue

        if not tool_calls:
            if (
                research_tool_calls >= PLANNER_MIN_RESEARCH_TOOL_CALLS
                or round_idx >= 1
            ) and _planner_text_indicates_blocker(last_parsed):
                return last_parsed
            if round_idx >= PLANNER_MAX_RESEARCH_ROUNDS - 1:
                break
            conversation.append(AIMessage(content=response_text or "{}"))
            if research_tool_calls >= PLANNER_MIN_RESEARCH_TOOL_CALLS:
                conversation.append(
                    HumanMessage(
                        content=(
                            "Research probes are complete. If migration cannot proceed, "
                            "respond with operator_input_request JSON (not repeated thinking). "
                            "Otherwise use tool_calls for any remaining checks."
                        )
                    )
                )
            else:
                if dry_run:
                    probe_hint = (
                        "Use tool_calls to research ADO source and expected GitHub targets. "
                        "Dry-run execution is simulated — focus on readiness and blockers."
                    )
                else:
                    probe_hint = (
                        "Use tool_calls to research ADO source and GitHub target state. "
                        "Live execution will write to GitHub — identify blockers now."
                    )
                conversation.append(HumanMessage(content=probe_hint))
            continue

        conversation.append(AIMessage(content=response_text or json.dumps(last_parsed)))
        batch_results: list[dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tool_name = str(tc.get("name", ""))
            if tool_name in _PLANNER_RESEARCH_TOOLS:
                research_tool_calls += 1
                _emit_tool_call(session, tool_name, subagent="planner", arguments=tc.get("arguments") or {})
            batch_results.append(
                await _execute_planner_tool_call(tc, session, accel_get, session_token)
            )

        conversation.append(
            HumanMessage(
                content=json.dumps(
                    {
                        "tool_results": batch_results,
                        "research_tool_calls_so_far": research_tool_calls,
                        "min_required": PLANNER_MIN_RESEARCH_TOOL_CALLS,
                    },
                    default=str,
                )[:12000]
            )
        )

    return last_parsed


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """Planner node — generates structured migration plans.

    Binds planner tools via bind_tools(), uses astream() for planning thoughts,
    generates MigrationPlan with topological sort, stores in AgentState.migration_plan.
    Handles validation_feedback for revised plans and sets pending_clarification
    when discovery data is insufficient.
    """
    session = state.get("session") or {}
    _transition_session(session, SessionState.PLANNING)
    try:
        return await _planner_node_impl(state, session)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        _append_and_stream(
            session,
            role="system",
            content=f"Planner error: {exc}\n{tb}",
            subagent="planner",
        )
        return {
            "error": str(exc),
            "should_return": True,
            "pending_clarification": None,
        }


async def _planner_post_validation_handoff(
    state: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any] | None:
    """Route validator outcomes through planner (validator → planner only)."""
    validation = state.get("validation_result")
    if validation is None:
        return None

    feedback = state.get("validation_feedback")
    passed = bool(validation.get("passed"))

    if passed and not feedback:
        migration_queue = state.get("migration_queue")
        if migration_queue:
            queue_items = migration_queue.get("items", [])
            current_index = int(migration_queue.get("current_index", 0) or 0)
            if queue_items and current_index < len(queue_items):
                _append_and_stream(
                    session,
                    role="system",
                    content=(
                        f"Planner: validation passed — continuing queue "
                        f"({current_index + 1}/{len(queue_items)})."
                    ),
                    subagent="planner",
                )
                return {
                    "planner_next": "executor",
                    "start_execution": True,
                    "migration_plan": state.get("migration_plan") or session.get("migration_plan"),
                    "migration_queue": migration_queue,
                    "should_return": False,
                }
            if queue_items:
                _append_and_stream(
                    session,
                    role="system",
                    content="Planner: migration queue complete — handing results to orchestrator.",
                    subagent="planner",
                )
        _append_and_stream(
            session,
            role="system",
            content="Planner: validation complete — handing results to orchestrator.",
            subagent="planner",
        )
        return {"planner_next": "orchestrator", "should_return": False}

    if feedback:
        failures = (
            (feedback.get("failures") or [])
            if isinstance(feedback, dict)
            else []
        )
        dry_run = bool(
            (validation or {}).get("dry_run", session.get("dry_run", True))
        )
        from ado2gh.agents.migration_agent.operator_input import failures_require_operator_escalation

        if failures and failures_require_operator_escalation(failures):
            session.pop("start_execution", None)
            _append_and_stream(
                session,
                role="system",
                content="Planner: operator action required — handing results to orchestrator.",
                subagent="planner",
            )
            return {
                "planner_next": "orchestrator",
                "should_return": False,
                "start_execution": False,
            }
        if failures and _all_failures_benign(failures, dry_run=dry_run):
            _append_and_stream(
                session,
                role="system",
                content=(
                    "Planner: validation reported only non-retryable dry-run skips — "
                    "continuing without replan."
                ),
                subagent="planner",
            )
            return {
                "planner_next": "orchestrator",
                "should_return": False,
                "validation_feedback": None,
            }
        if isinstance(feedback, dict) and (
            feedback.get("escalate") or not feedback.get("retry_recommended", True)
        ):
            session.pop("start_execution", None)
            session["pev_max_retries_exhausted"] = True
            _append_and_stream(
                session,
                role="system",
                content="Planner: validation failed after max retries — handing to orchestrator.",
                subagent="planner",
            )
            return {
                "planner_next": "orchestrator",
                "should_return": False,
                "start_execution": False,
            }
        return None

    session.pop("start_execution", None)
    session["pev_max_retries_exhausted"] = True
    _append_and_stream(
        session,
        role="system",
        content="Planner: validation failed after max retries — handing to orchestrator.",
        subagent="planner",
    )
    return {
        "planner_next": "orchestrator",
        "should_return": False,
        "start_execution": False,
    }


async def _planner_node_impl(state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Planner node implementation — wrapped by planner_node for error handling."""
    post_validation = await _planner_post_validation_handoff(state, session)
    if post_validation is not None:
        return post_validation

    validation_feedback = state.get("validation_feedback")
    if validation_feedback and isinstance(validation_feedback, str):
        try:
            validation_feedback = json.loads(validation_feedback)
        except Exception:
            validation_feedback = None
    if (
        isinstance(validation_feedback, dict)
        and validation_feedback.get("failures")
        and not session.get("pending_operator_input")
    ):
        dry_run = bool(session.get("dry_run", True))
        if _all_failures_benign(validation_feedback["failures"], dry_run=dry_run):
            validation_feedback = None
        else:
            from ado2gh.agents.migration_agent.operator_input import (
                assess_operator_input_needed,
                store_operator_input,
            )

            existing_plan = state.get("migration_plan") or session.get("migration_plan") or {}
            if isinstance(existing_plan, str):
                try:
                    existing_plan = json.loads(existing_plan)
                except Exception:
                    existing_plan = {}
            op_req = assess_operator_input_needed(
                plan=existing_plan if isinstance(existing_plan, dict) else {},
                validation_feedback=validation_feedback,
                session=session,
            )
            if op_req:
                store_operator_input(session, op_req)
                _append_and_stream(
                    session,
                    role="system",
                    content="Planner: validator blocker requires operator input.",
                    subagent="planner",
                )
                return {
                    "pending_clarification": {
                        "message_type": "operator_input_required",
                        "payload": op_req.model_dump(),
                    },
                    "pending_operator_input": op_req.model_dump(),
                    "planner_next": "orchestrator",
                    "should_return": False,
                }

    # Approved execution handoff from orchestrator — pass existing plan through to executor
    existing_plan = state.get("migration_plan") or session.get("migration_plan")
    if (
        state.get("start_execution")
        and session.get("plan_approved")
        and existing_plan
        and not session.get("pev_max_retries_exhausted")
        and not _is_pev_max_retries_exhausted(state, session)
    ):
        if isinstance(existing_plan, str):
            try:
                existing_plan = json.loads(existing_plan)
            except Exception:
                existing_plan = None
        if isinstance(existing_plan, dict) and existing_plan.get("repos"):
            _append_and_stream(
                session,
                role="system",
                content="Planner: approved plan ready — handing off to executor.",
                subagent="planner",
            )
            return {
                "migration_plan": existing_plan,
                "migration_queue": state.get("migration_queue") or session.get("migration_queue"),
                "start_execution": True,
                "planner_next": "executor",
                "should_return": False,
            }

    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    capabilities = state.get("capabilities")
    accel_get = state.get("accel_get")
    accel_post = state.get("accel_post")
    session_token = state.get("session_token")
    validation_feedback = state.get("validation_feedback")
    if validation_feedback and isinstance(validation_feedback, str):
        try:
            validation_feedback = json.loads(validation_feedback)
        except Exception:
            validation_feedback = None
    iteration = state.get("iteration", 0)

    _append_and_stream(
        session,
        role="system",
        content="Planner: generating migration plan…",
        subagent="planner",
    )

    # Increment iteration for PEV cycle tracking
    iteration += 1

    # If we have validation feedback, this is a revised plan
    revision = 0
    existing_plan = state.get("migration_plan") or session.get("migration_plan")
    if existing_plan and isinstance(existing_plan, str):
        try:
            existing_plan = json.loads(existing_plan)
        except Exception:
            existing_plan = None
    if existing_plan and isinstance(existing_plan, dict):
        revision = existing_plan.get("revision", 0)
    if validation_feedback:
        revision += 1
        _append_and_stream(
            session,
            role="system",
            content=f"Planner: revising plan (revision {revision}) based on validator feedback…",
            subagent="planner",
        )

    # Load discovery data if not already in session
    discovery = session.get("discovery_snapshot")
    if discovery and isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = None
    if not discovery:
        if accel_get:
            try:
                profile_id = session.get("profile_id", "lightweight")
                _append_and_stream(
                    session,
                    role="system",
                    content="Planner: loading discovery data…",
                    subagent="planner",
                )
                discovery = await accel_get(
                    f"/v1/settings/profiles/{profile_id}/discovery",
                    session_token=session_token,
                )
                session["discovery_snapshot"] = discovery
                from datetime import datetime, timezone
                session["discovery_fetched_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                _append_and_stream(
                    session,
                    role="system",
                    content=f"Planner: failed to load discovery data: {e}",
                    subagent="planner",
                )
                discovery = {}
        else:
            discovery = {}

    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []

    # Fallback: if discovery failed but we have requested repo(s), create
    # synthetic repo entries so the plan isn't empty.
    # Support both single-repo (plan_repository_id) and bulk (plan_repository_ids) modes.
    requested_repos = session.get("plan_repository_ids") or state.get("plan_repository_ids")
    if not requested_repos:
        single_repo = session.get("plan_repository_id") or state.get("plan_repository_id")
        if single_repo:
            requested_repos = [single_repo]

    if not repos and requested_repos and not session.get("discovery_fetched_at"):
        synth_repos = []
        for repo_id in requested_repos:
            parts = repo_id.split("/")
            repo_name = parts[-1] if len(parts) > 1 else repo_id
            project = parts[0] if len(parts) > 1 else ""
            synth_repos.append({
                "id": repo_id,
                "name": repo_name,
                "project": project,
                "repo_name": repo_name,
            })
        repos = synth_repos
        _append_and_stream(
            session,
            role="system",
            content=(
                f"Planner: discovery unavailable — using {len(requested_repos)} requested repo(s) directly: "
                f"{', '.join(requested_repos)}."
            ),
            subagent="planner",
        )
    elif repos and requested_repos:
        # Filter repos to only the requested ones; reject unknown repos when discovery loaded
        filtered = []
        not_found: list[str] = []
        for req_id in requested_repos:
            match = find_discovery_repo(req_id, repos)
            if match:
                filtered.append(normalize_discovery_repo(match))
            else:
                not_found.append(req_id)

        if not_found:
            clarification = repo_not_found_clarification(not_found[0], discovery if isinstance(discovery, dict) else None)
            _append_and_stream(
                session,
                role="system",
                content=clarification["payload"]["message"],
                subagent="planner",
            )
            return {
                "pending_clarification": clarification,
                "iteration": iteration,
                "should_return": False,
            }
        repos = filtered

    if not repos:
        clarification = {
            "to_role": "orchestrator",
            "type": "clarification_request",
            "payload": {
                "message": (
                    "Planner needs discovery data or a specific repository to build a migration plan. "
                    "Run discovery on the profile or specify which repo to migrate."
                ),
                "error": "no_repos",
            },
        }
        _append_and_stream(
            session,
            role="system",
            content=clarification["payload"]["message"],
            subagent="planner",
        )
        return {
            "pending_clarification": clarification,
            "iteration": iteration,
            "should_return": False,
        }

    repo_keys = [
        canonical_repo_id(r) if isinstance(r, dict) else str(r)
        for r in repos
        if (canonical_repo_id(r) if isinstance(r, dict) else str(r))
    ]
    from ado2gh.agents.migration_agent.scope_executor import ensure_repo_feature_detection
    await ensure_repo_feature_detection(session, repo_keys, accel_get, session_token)

    baseline_findings = await _gather_planner_baseline_context(
        session, [r for r in repos if isinstance(r, dict)], accel_get, session_token,
    )

    from ado2gh.agents.migration_agent.operator_input import (
        assess_operator_input_needed,
        blockers_from_baseline_probes,
        operator_input_from_probe_failures,
        store_operator_input,
    )

    probe_blockers = blockers_from_baseline_probes(baseline_findings)
    if probe_blockers and not validation_feedback:
        plan = _build_heuristic_plan(repos, session, revision)
        from ado2gh.agents.migration_agent.pipeline_plan import finalize_agent_migration_plan
        plan = finalize_agent_migration_plan(plan, session)
        session["migration_plan"] = plan
        migration_queue = _build_migration_queue_from_plan(plan)
        op_req = operator_input_from_probe_failures(probe_blockers, session)
        _append_and_stream(
            session,
            role="system",
            content=(
                "Planner: repository verification failed — requesting operator decision "
                "before building an execution plan."
            ),
            subagent="planner",
        )
        return _planner_operator_input_return(
            session, plan, migration_queue, iteration, op_req,
        )

    parsed: dict[str, Any] = {}
    # Build plan using LLM if available
    if llm and not llm_unconfigured:
        from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools

        planner_tools = get_planner_tools(
            accel_get=accel_get,
            accel_post=accel_post,
            session_token=session_token,
            session_getter=lambda: session,
        )
        system_prompt = get_prompt("planner")

        # Build context with discovery data and validation feedback
        dry_run = bool(session.get("dry_run", True))
        context_parts = [
            f"Target repository keys: {json.dumps(repo_keys, default=str)}",
            f"Discovery repos (sample): {json.dumps(repos[:20], default=str)}",
            f"Baseline research (auto): {json.dumps(baseline_findings, default=str)[:6000]}",
            f"Dry run: {dry_run}",
        ]
        if dry_run:
            context_parts.append(
                "DRY RUN: Plan for simulated execution. The Executor will not publish to GitHub; "
                "the Validator will judge executor logs and simulated workflow output, not live targets."
            )
        else:
            context_parts.append(
                "LIVE RUN: Plan for real execution. The Executor performs writes; "
                "the Validator verifies outcomes via APIs only — not executor status logs."
            )
        context_parts.append(
            "Surface blockers and assumptions before execution."
        )
        if validation_feedback:
            context_parts.append(f"Validator feedback: {json.dumps(validation_feedback, default=str)}")
        if existing_plan:
            context_parts.append(f"Existing plan revision {revision - 1}: {json.dumps(existing_plan, default=str)[:2000]}")

        # T097: Apply context window trimming
        existing_messages = state.get("messages", [])
        cycle_summaries = state.get("cycle_summaries", [])
        max_budget = state.get("max_token_budget", 32000)

        new_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n".join(context_parts)),
        ]

        all_messages = existing_messages + new_messages
        trimmed_messages = build_context_with_cycle_summaries(
            all_messages, cycle_summaries, max_budget
        )

        llm_with_tools = llm
        if capabilities and capabilities.supports_tool_calling and planner_tools:
            try:
                llm_with_tools = llm.bind_tools(planner_tools)
            except Exception:
                pass

        parsed = await _run_planner_research_loop(
            state,
            session,
            llm_with_tools,
            trimmed_messages,
            capabilities=capabilities,
            accel_get=accel_get,
            session_token=session_token,
            validation_feedback=validation_feedback if isinstance(validation_feedback, dict) else None,
            dry_run=dry_run,
        )

        if parsed.get("repos"):
            plan = _build_migration_plan_from_llm(parsed, session, revision)
            if parsed.get("risk_summary"):
                assumptions = list(plan.get("assumptions") or [])
                assumptions.append(f"Planner risk summary: {parsed['risk_summary']}")
                plan["assumptions"] = assumptions
        else:
            _append_and_stream(
                session,
                role="system",
                content=(
                    "Planner: research loop did not return a structured plan — "
                    "building deterministic plan from discovery and baseline probes."
                ),
                subagent="planner",
            )
            plan = _build_heuristic_plan(repos, session, revision)
            if baseline_findings:
                plan.setdefault("assumptions", []).append(
                    f"Baseline probes: {json.dumps(baseline_findings, default=str)[:1500]}"
                )
    else:
        # No LLM — use heuristic plan builder
        plan = _build_heuristic_plan(repos, session, revision)
        if baseline_findings:
            plan.setdefault("assumptions", []).append(
                f"Baseline probes: {json.dumps(baseline_findings, default=str)[:1500]}"
            )

    # Store plan in state and session
    session["migration_plan"] = plan
    from ado2gh.agents.migration_agent.pipeline_plan import finalize_agent_migration_plan
    plan = finalize_agent_migration_plan(plan, session)
    session["migration_plan"] = plan

    migration_queue = _build_migration_queue_from_plan(plan)
    queue_items = migration_queue.get("items", [])

    planner_op_request = parsed.get("operator_input_request") if (llm and parsed) else None
    if not planner_op_request and parsed and _planner_text_indicates_blocker(parsed):
        blocker_list = blockers_from_baseline_probes(baseline_findings)
        if not blocker_list:
            repo = repo_keys[0] if repo_keys else session.get("plan_repository_id", "")
            blocker_list = [{
                "repo": repo,
                "scope": "repo",
                "blocker": str(parsed.get("thinking") or "Planner could not verify repositories."),
                "key": f"repo:{repo}:planner_blocker",
            }]
        planner_op_request = operator_input_from_probe_failures(blocker_list, session).model_dump()

    vf_dict = validation_feedback if isinstance(validation_feedback, dict) else None
    op_req = assess_operator_input_needed(
        plan=plan,
        validation_feedback=vf_dict,
        session=session,
        planner_request=planner_op_request,
    )
    if op_req and not session.get("plan_approved"):
        return _planner_operator_input_return(
            session, plan, migration_queue, iteration, op_req,
        )

    _append_and_stream(
        session,
        role="system",
        content=f"Planner: plan generated — {len(plan.get('repos', []))} repo(s), revision {revision}. Queue: {len(queue_items)} item(s).",
        subagent="planner",
    )

    return {
        "migration_plan": plan,
        "migration_queue": migration_queue,
        "iteration": iteration,
        "pending_clarification": None,
        "should_return": False,
        "start_pev": False,
        "planner_next": "orchestrator",
    }


def _build_migration_queue_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Build a migration queue from a plan's repos and work items."""
    plan_repos = plan.get("repos", [])
    work_items = [wi for wi in plan.get("work_items", []) if isinstance(wi, dict)]

    by_repo: dict[str, list[dict[str, Any]]] = {}
    for wi in work_items:
        repo_id = str(wi.get("repo") or "").strip()
        if repo_id:
            by_repo.setdefault(repo_id, []).append(wi)

    plan_ids: list[str] = []
    for repo in plan_repos:
        repo_id = canonical_repo_id(repo) if isinstance(repo, dict) else str(repo).strip()
        if repo_id and repo_id not in plan_ids:
            plan_ids.append(repo_id)

    if by_repo:
        repo_order: list[str] = []
        for repo_id in plan_ids:
            if repo_id in by_repo and repo_id not in repo_order:
                repo_order.append(repo_id)
        for repo_id in by_repo:
            if repo_id not in repo_order:
                repo_order.append(repo_id)
    else:
        repo_order = plan_ids

    queue_items = [
        {
            "repo_id": repo_id,
            "work_items": by_repo.get(repo_id, []),
            "status": "pending",
        }
        for repo_id in repo_order
    ]
    return {
        "items": queue_items,
        "current_index": 0,
        "completed": [],
        "failed": [],
    }


def _advance_migration_queue(
    migration_queue: dict[str, Any],
    *,
    repo_id: str = "",
) -> bool:
    """Advance queue after one repo slot is consumed. Returns True if advanced."""
    queue_items = migration_queue.get("items", [])
    current_index = int(migration_queue.get("current_index", 0) or 0)
    if current_index >= len(queue_items):
        return False
    migration_queue["current_index"] = current_index + 1
    completed = list(migration_queue.get("completed", []))
    resolved_repo = repo_id or str(queue_items[current_index].get("repo_id") or "").strip()
    if resolved_repo and resolved_repo not in completed:
        completed.append(resolved_repo)
    migration_queue["completed"] = completed
    return True


def _build_heuristic_plan(
    repos: list[dict[str, Any]],
    session: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    """Build a migration plan using topological sort and migrate-tab pipeline steps."""
    from ado2gh.agents.migration_agent.pipeline_plan import (
        finalize_agent_migration_plan,
        pipeline_counts_from_discovery,
        repo_config_from_discovery,
    )
    from ado2gh.api.migration_work_plan import build_work_items_for_repos

    dry_run = session.get("dry_run", True)

    sorted_repos = []
    remaining = list(repos)
    processed = set()

    for _ in range(len(repos) + 1):
        if not remaining:
            break
        for repo in list(remaining):
            repo_id = canonical_repo_id(repo) if isinstance(repo, dict) else str(repo)
            deps = repo.get("dependencies", []) if isinstance(repo, dict) else []
            if all(d in processed for d in deps):
                sorted_repos.append(repo)
                processed.add(repo_id)
                remaining.remove(repo)

    sorted_repos.extend(remaining)

    repo_configs = [
        repo_config_from_discovery(r, session) for r in sorted_repos if isinstance(r, dict)
    ]
    pipeline_counts = pipeline_counts_from_discovery(session.get("discovery_snapshot"))
    from ado2gh.agents.migration_agent.scope_executor import build_agent_work_items_for_session

    work_items = build_agent_work_items_for_session(
        repo_configs,
        session,
        db=None,
        repo_pipeline_counts=pipeline_counts,
    ) if repo_configs else []

    plan_repos: list[dict[str, Any]] = []
    for repo_dict, cfg in zip(
        [r for r in sorted_repos if isinstance(r, dict)],
        repo_configs,
    ):
        entry: dict[str, Any] = {
            "id": canonical_repo_id(repo_dict),
            "name": repo_dict.get("name") or repo_dict.get("repo_name", ""),
            "gh_org": cfg.gh_org,
            "gh_repo": cfg.gh_repo,
            "github_org": cfg.gh_org,
            "github_repo": cfg.gh_repo,
        }
        plan_repos.append(entry)

    plan = {
        "repos": plan_repos,
        "work_items": work_items,
        "dry_run": dry_run,
        "assumptions": [
            "Plan follows the Migrate tab pipeline: discovery → dependencies → "
            "migrate repos → convert workflows → validate",
        ],
        "blocked_items": [r for r in repos if isinstance(r, dict) and r.get("blocked")],
        "revision": revision,
        "repo_count": len(sorted_repos),
    }
    return finalize_agent_migration_plan(plan, session)


def _build_migration_plan_from_llm(
    parsed: dict[str, Any],
    session: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    """Build a migration plan from LLM-parsed JSON output."""
    from ado2gh.agents.migration_agent.pipeline_plan import (
        finalize_agent_migration_plan,
        pipeline_counts_from_discovery,
        repo_config_from_discovery,
    )
    from ado2gh.api.migration_work_plan import build_work_items_for_repos

    raw_repos = parsed.get("repos", [])
    repos = [
        {
            "id": r.get("id", r.get("name", "")),
            "name": r.get("name", ""),
            "project": r.get("project", ""),
            "repo_name": r.get("repo_name", r.get("name", "")),
        }
        if isinstance(r, dict)
        else {"id": str(r), "name": str(r), "project": "", "repo_name": str(r)}
        for r in raw_repos
    ]
    raw_work_items = parsed.get("work_items", [])
    repo_configs = [repo_config_from_discovery(r, session) for r in repos]
    pipeline_counts = pipeline_counts_from_discovery(session.get("discovery_snapshot"))
    if repo_configs:
        from ado2gh.agents.migration_agent.scope_executor import build_agent_work_items_for_session

        work_items = build_agent_work_items_for_session(
            repo_configs,
            session,
            db=None,
            repo_pipeline_counts=pipeline_counts,
            plan=parsed,
        )
    elif raw_work_items and all(
        isinstance(wi, dict) and wi.get("label") for wi in raw_work_items
    ):
        work_items = [
            wi if isinstance(wi, dict)
            else {"repo": str(wi), "scope": MigrationScope.REPO.value, "status": "ready"}
            for wi in raw_work_items
        ]
    else:
        work_items = []
    plan = {
        "repos": repos,
        "work_items": work_items,
        "dry_run": parsed.get("dry_run", session.get("dry_run", True)),
        "assumptions": parsed.get("assumptions", []),
        "blocked_items": parsed.get("blocked_items", []),
        "revision": revision,
        "repo_count": len(repos),
        "pipeline_step_ids": parsed.get("pipeline_step_ids"),
    }
    return finalize_agent_migration_plan(plan, session)


# ─── Node: executor ───────────────────────────────────────────────────

_AGENT_WRITE_SCOPES = frozenset({
    "git", "repo", "pipelines", "secrets", "service_connections",
    "boards", "work_items", "branch_policies", "test_plans", "artifacts", "wiki",
})


async def _execute_deterministic_repo_scopes(
    repo_id: str,
    ready_items: list[dict[str, Any]],
    migration_plan: dict[str, Any],
    *,
    accel_get: Any,
    accel_post: Any,
    session_token: str | None,
    dry_run: bool,
    session: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run only ready plan scopes for one repository."""
    from ado2gh.agents.migration_agent.pipeline_plan import work_item_scopes
    from ado2gh.agents.migration_agent.utils import canonical_plan_repo_key

    discovery = session.get("discovery_snapshot")
    if isinstance(discovery, str):
        import json
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    repo_id = canonical_plan_repo_key(
        repo_id,
        discovery if isinstance(discovery, dict) else None,
    )
    scope_labels = [str(wi.get("scope") or "") for wi in ready_items if wi.get("scope")]
    if scope_labels:
        _append_and_stream(
            session,
            role="system",
            content=f"Executor: processing {repo_id} (scopes: {', '.join(scope_labels)})…",
            subagent="executor",
        )

    repo_result: dict[str, Any] = {"repo": repo_id, "scopes": {}}
    failures: list[dict[str, Any]] = []
    rollback_records: list[dict[str, Any]] = []

    for wi in ready_items:
        for scope in work_item_scopes(wi):
            scope_result = await _execute_scope(
                scope,
                wi,
                migration_plan,
                accel_get,
                accel_post,
                session_token,
                dry_run,
                session,
            )
            repo_result["scopes"][scope] = scope_result
            if scope_result.get("error") and scope_result.get("status") not in (
                "skipped",
                "pending",
            ):
                failures.append({
                    "repo": repo_id,
                    "scope": scope,
                    "error": scope_result["error"],
                    "error_code": scope_result.get("error_code", "execution_error"),
                })
            if (
                scope in _AGENT_WRITE_SCOPES
                and not scope_result.get("error")
                and not dry_run
            ):
                rollback_records.append({
                    "resource_type": scope,
                    "resource_name": repo_id,
                    "github_org": wi.get("github_org", ""),
                    "correlation_id": f"{repo_id}:{scope}",
                })
    return repo_result, failures, rollback_records


async def executor_node(state: dict[str, Any]) -> dict[str, Any]:
    """Executor node — performs migration operations deterministically.

    Binds executor tools, streams execution thoughts, executes migration
    operations, produces ExecutorResult, tracks RollbackRecord entries,
    handles idempotency, routes via accelerator API for existing ops and
    direct API for new resource types.
    """
    session = state.get("session") or {}
    _transition_session(session, SessionState.EXECUTING)
    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    capabilities = state.get("capabilities")
    accel_get = state.get("accel_get")
    accel_post = state.get("accel_post")
    session_token = state.get("session_token")
    migration_plan = state.get("migration_plan") or session.get("migration_plan")
    iteration = state.get("iteration", 0)

    iteration += 1

    migration_plan_early = state.get("migration_plan") or session.get("migration_plan")
    dry_run_early = True
    if isinstance(migration_plan_early, dict):
        dry_run_early = bool(migration_plan_early.get("dry_run", session.get("dry_run", True)))
    else:
        dry_run_early = bool(session.get("dry_run", True))

    _append_and_stream(
        session,
        role="system",
        content=(
            "Executor: starting migration operations in dry-run (simulated) mode…"
            if dry_run_early
            else "Executor: starting migration operations in live mode…"
        ),
        subagent="executor",
    )

    if not migration_plan:
        _append_and_stream(
            session,
            role="system",
            content="Executor: no migration plan — sending failure to validator.",
            subagent="executor",
        )
        return {
            "executor_result": {
                "per_repo_results": [],
                "failures": [{
                    "error": "No migration plan available for execution.",
                    "error_code": "no_plan",
                }],
                "skipped": [],
                "dry_run": session.get("dry_run", True),
            },
            "iteration": iteration,
        }

    repos = migration_plan.get("repos", [])
    work_items = migration_plan.get("work_items", [])
    dry_run = migration_plan.get("dry_run", session.get("dry_run", True))
    session["dry_run"] = dry_run

    from ado2gh.agents.migration_agent.pipeline_executor import execute_repo_migration

    def _pipeline_progress(label: str, status: str, run: dict[str, Any]) -> None:
        run_id = str(run.get("id") or session.get("run_id") or "")
        if label == "Monitoring run created" and run_id:
            _append_and_stream(
                session,
                role="system",
                content=f"Executor: created pipeline run {run_id} for monitoring.",
                subagent="executor",
            )
            return
        if label:
            _append_and_stream(
                session,
                role="system",
                content=f"Pipeline: {label} ({status})…",
                subagent="executor",
            )

    async def _run_repo(repo_id: str, ready_items: list[dict[str, Any]]):
        return await execute_repo_migration(
            repo_id,
            ready_items,
            migration_plan,
            session,
            accel_get=accel_get,
            accel_post=accel_post,
            session_token=session_token,
            dry_run=dry_run,
            on_progress=_pipeline_progress,
            scope_executor=_execute_deterministic_repo_scopes,
        )

    # T106: Bind executor tools to LLM for tool-calling
    executor_tools = None
    if llm and not llm_unconfigured and capabilities and capabilities.supports_tool_calling:
        try:
            from ado2gh.agents.migration_agent.tools import get_executor_tools
            from ado2gh.agents.migration_agent.route_helpers import _accel_request_impl
            build_plan = state.get("build_plan")

            async def _accel_request(method: str, path: str, body: dict | None = None):
                return await _accel_request_impl(
                    method, path, body, session_token=session_token,
                )

            executor_tools = get_executor_tools(
                accel_get,
                accel_post,
                session_token,
                build_plan=build_plan,
                session_getter=lambda: session,
                accel_request=_accel_request,
            )
        except Exception:
            pass

    # Bind tools to LLM if tool calling is supported
    llm_with_tools = llm
    if capabilities and capabilities.supports_tool_calling and executor_tools:
        try:
            llm_with_tools = llm.bind_tools(executor_tools)
        except Exception:
            pass

    # T107: Use LLM-driven execution when available
    # Disabled: LLM tool-calling protocol not fully integrated — deterministic
    # execution is reliable and calls _execute_scope directly.
    use_llm_execution = False

    # T099: Check for migration queue for sequential processing
    migration_queue = state.get("migration_queue")
    if migration_queue:
        queue_items = migration_queue.get("items", [])
        current_index = migration_queue.get("current_index", 0)
        completed = migration_queue.get("completed", [])
        failed = migration_queue.get("failed", [])

        # Process one repo at a time
        if current_index < len(queue_items):
            queue_item = queue_items[current_index]
            discovery = session.get("discovery_snapshot")
            if isinstance(discovery, str):
                try:
                    discovery = json.loads(discovery)
                except Exception:
                    discovery = {}
            if not isinstance(discovery, dict):
                discovery = {}
            repo_id = canonical_plan_repo_key(
                str(queue_item.get("repo_id", "") or "").strip(),
                discovery,
            )
            item_work_items = queue_item.get("work_items", [])
            if not repo_id and item_work_items:
                repo_id = canonical_plan_repo_key(
                    str(item_work_items[0].get("repo") or "").strip(),
                    discovery,
                )

            per_repo_results = []
            failures = []
            skipped = []
            rollback_records = []

            from ado2gh.api.migration_work_plan import executable_work_items

            for wi in item_work_items:
                if wi.get("status") == "blocked":
                    skipped.append({
                        "repo": repo_id,
                        "reason": "blocked",
                        "details": wi.get("blocked_reasons", []),
                    })

            ready_items = executable_work_items(item_work_items)
            session_id = session.get("session_id", "")
            if ready_items:
                if session_id:
                    try:
                        from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
                        store = MigrationSessionStore()
                        if not store.acquire_repo_lock(session_id, repo_id):
                            holder = store.repo_lock_holder(repo_id)
                            return _executor_result_for_repo_lock(
                                session,
                                repo_id=repo_id,
                                lock_holder_session_id=holder,
                                dry_run=dry_run,
                                iteration=iteration,
                                migration_queue=migration_queue,
                                failed=failed,
                            )
                    except Exception:
                        pass

                if use_llm_execution:
                    _append_and_stream(
                        session,
                        role="system",
                        content=(
                            f"Executor: LLM execution not fully integrated — "
                            f"using deterministic path for {repo_id}."
                        ),
                        subagent="executor",
                    )

                repo_result, wi_failures, wi_rollbacks = await _run_repo(
                    repo_id,
                    ready_items,
                )
                per_repo_results.append(repo_result)
                failures.extend(wi_failures)
                rollback_records.extend(wi_rollbacks)

                if session_id:
                    try:
                        from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
                        store = MigrationSessionStore()
                        store.release_repo_lock(session_id, repo_id)
                    except Exception:
                        pass
            else:
                for wi in item_work_items:
                    status = wi.get("status", "ready")
                    if status in ("blocked", "skipped"):
                        skipped.append({
                            "repo": repo_id or wi.get("repo", ""),
                            "scope": wi.get("scope", ""),
                            "reason": status,
                            "details": [wi.get("blocker") or f"Work item status: {status}"],
                        })
                _append_and_stream(
                    session,
                    role="system",
                    content=(
                        f"Executor: no ready scopes for {repo_id or 'repository'} "
                        f"— skipping execution ({len(skipped)} item(s) not ready)."
                    ),
                    subagent="executor",
                )

            # T100: Don't advance queue index yet - let validator run first
            # Queue index will be advanced after validation passes
            executor_result = {
                "per_repo_results": per_repo_results,
                "failures": failures,
                "skipped": skipped,
                "dry_run": dry_run,
                "current_repo_id": repo_id,  # Track which repo was processed
            }

            _append_event(
                session,
                role="system",
                content=f"Executor: processed {repo_id} — awaiting validation (queue progress: {current_index + 1}/{len(queue_items)})",
                kind="thinking",
                subagent="executor",
            )

            return {
                "executor_result": executor_result,
                "rollback_records": rollback_records,
                "migration_queue": migration_queue,
                "iteration": iteration,
                "pending_clarification": None,
                "should_return": False,
            }
        else:
            # Queue already exhausted — do not re-run validation loop
            _append_and_stream(
                session,
                role="system",
                content="Executor: all queue items already processed.",
                subagent="executor",
            )
            return {
                "migration_queue": migration_queue,
                "iteration": iteration,
                "pending_clarification": None,
                "should_return": True,
            }

    # Fallback: process all work items (non-queue mode)
    from ado2gh.api.migration_work_plan import executable_work_items, group_work_items_by_repo

    per_repo_results = []
    failures = []
    skipped = []
    rollback_records = []

    for wi in work_items:
        if wi.get("status") == "blocked":
            skipped.append({
                "repo": wi.get("repo", ""),
                "reason": "blocked",
                "details": wi.get("blocked_reasons", []),
            })

    for repo_id, repo_ready_items in group_work_items_by_repo(
        executable_work_items(work_items)
    ).items():
        session_id = session.get("session_id", "")
        if session_id:
            try:
                from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
                store = MigrationSessionStore()
                if not store.acquire_repo_lock(session_id, repo_id):
                    holder = store.repo_lock_holder(repo_id)
                    lock_result = _executor_result_for_repo_lock(
                        session,
                        repo_id=repo_id,
                        lock_holder_session_id=holder,
                        dry_run=dry_run,
                        iteration=iteration,
                    )
                    failures.extend(lock_result["executor_result"]["failures"])
                    skipped.extend(lock_result["executor_result"]["skipped"])
                    continue
            except Exception:
                pass

        repo_result, repo_failures, repo_rollbacks = await _run_repo(
            repo_id,
            repo_ready_items,
        )
        per_repo_results.append(repo_result)
        failures.extend(repo_failures)
        rollback_records.extend(repo_rollbacks)

        if session_id:
            try:
                from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
                store = MigrationSessionStore()
                store.release_repo_lock(session_id, repo_id)
            except Exception:
                pass

    executor_result = {
        "per_repo_results": per_repo_results,
        "failures": failures,
        "skipped": skipped,
        "dry_run": dry_run,
    }

    _append_and_stream(
        session,
        role="system",
        content=f"Executor: complete — {len(per_repo_results)} repo(s) processed, {len(failures)} failure(s), {len(skipped)} skipped.",
        subagent="executor",
    )

    return {
        "executor_result": executor_result,
        "rollback_records": rollback_records,
        "iteration": iteration,
        "pending_clarification": None,
        "should_return": False,
    }


async def _execute_scope(
    scope: str,
    work_item: dict[str, Any],
    plan: dict[str, Any],
    accel_get: Any,
    accel_post: Any,
    session_token: str | None,
    dry_run: bool,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a single migration scope for a work item."""
    from ado2gh.agents.migration_agent.scope_executor import execute_migration_scope

    return await execute_migration_scope(
        scope,
        work_item,
        plan,
        accel_post=accel_post,
        session_token=session_token,
        dry_run=dry_run,
        secret_mappings=(session or {}).get("operator_secret_mappings"),
        session=session,
    )


# ─── Node: validator ──────────────────────────────────────────────────

def _validator_executor_log_summary(executor_result: dict[str, Any]) -> dict[str, Any]:
    """Executor output used as primary evidence in dry-run validation."""
    return {
        "dry_run": executor_result.get("dry_run", True),
        "per_repo_results": executor_result.get("per_repo_results"),
        "failures": executor_result.get("failures"),
        "skipped": executor_result.get("skipped"),
        "pipeline_run_id": executor_result.get("pipeline_run_id"),
    }


def _validator_executor_metadata(executor_result: dict[str, Any]) -> dict[str, Any]:
    """Minimal executor metadata for live validation (not evidentiary)."""
    repos = [
        str(r.get("repo") or "")
        for r in (executor_result.get("per_repo_results") or [])
        if isinstance(r, dict) and r.get("repo")
    ]
    return {
        "dry_run": False,
        "repos_processed": repos,
        "failure_count": len(executor_result.get("failures") or []),
        "pipeline_run_id": executor_result.get("pipeline_run_id"),
    }


def _gather_validator_dry_run_evidence(
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build validator findings from executor logs only (dry-run)."""
    from ado2gh.agents.migration_agent.utils import canonical_plan_repo_key

    discovery = session.get("discovery_snapshot") or {}
    if isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    if not isinstance(discovery, dict):
        discovery = {}

    pipeline_counts: dict[str, int] = {}
    for pipe in discovery.get("pipelines") or []:
        if not isinstance(pipe, dict):
            continue
        repo_name = str(pipe.get("repo_name") or pipe.get("repository") or "")
        if repo_name:
            pipeline_counts[repo_name] = pipeline_counts.get(repo_name, 0) + 1

    findings: list[dict[str, Any]] = []
    for repo_result in executor_result.get("per_repo_results") or []:
        if not isinstance(repo_result, dict):
            continue
        repo_key = canonical_plan_repo_key(str(repo_result.get("repo") or ""), discovery)
        if not repo_key:
            continue

        scopes = repo_result.get("scopes") or {}
        entry: dict[str, Any] = {
            "repo": repo_key,
            "dry_run": True,
            "scopes_executed": list(scopes.keys()),
            "executor_scopes": scopes,
        }

        pipe_result = scopes.get("pipelines") if isinstance(scopes.get("pipelines"), dict) else {}
        if pipe_result and pipe_result.get("status") not in ("skipped", "pending"):
            wf_files = pipe_result.get("workflow_files") or pipe_result.get("workflows") or []
            entry["executor_workflow_count"] = len(wf_files) if isinstance(wf_files, list) else 0
            repo_short = repo_key.split("/", 1)[-1] if "/" in repo_key else repo_key
            ado_count = pipeline_counts.get(repo_short, 0)
            entry["discovery_pipeline_count"] = ado_count
            if ado_count > 0 and entry["executor_workflow_count"] == 0:
                entry["pipeline_conversion_gap"] = True

        findings.append(entry)

    session["validator_baseline_probes"] = findings
    return findings


def _validator_has_pipeline_scope(executor_result: dict[str, Any]) -> bool:
    for repo_result in executor_result.get("per_repo_results") or []:
        scopes = repo_result.get("scopes") or {}
        if "pipelines" in scopes and scopes["pipelines"].get("status") not in ("skipped", "pending"):
            return True
    return False


def _validator_text_indicates_blocker(parsed: dict[str, Any]) -> bool:
    return _planner_text_indicates_blocker(parsed)


def _validator_append_thinking(
    session: dict[str, Any],
    content: Any,
    seen: set[str],
) -> None:
    text = str(content or "").strip()
    if not text or text in seen:
        return
    seen.add(text)
    _append_and_stream(
        session,
        role="system",
        content=text,
        kind="thinking",
        subagent="validator",
    )


async def _gather_validator_baseline_probes(
    session: dict[str, Any],
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    accel_get: Any,
    session_token: str | None,
) -> list[dict[str, Any]]:
    """Deterministic pre-LLM validation probes for executed repos."""
    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )
    if dry_run:
        return _gather_validator_dry_run_evidence(
            executor_result,
            migration_plan if isinstance(migration_plan, dict) else None,
            session,
        )

    from ado2gh.agents.migration_agent.pipeline_plan import repo_config_from_discovery
    from ado2gh.agents.migration_agent.utils import canonical_plan_repo_key, find_discovery_repo

    findings: list[dict[str, Any]] = []
    discovery = session.get("discovery_snapshot") or {}
    if isinstance(discovery, str):
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    if not isinstance(discovery, dict):
        discovery = {}

    repos_list = discovery.get("repos") or []
    tools_by_name: dict[str, Any] = {}
    if accel_get:
        try:
            from ado2gh.agents.migration_agent.tools import get_validator_tools

            validator_tools = get_validator_tools(
                accel_get,
                session_token,
                session_getter=lambda: session,
            )
            tools_by_name = {t.name: t for t in validator_tools if getattr(t, "name", None)}
        except Exception:
            pass

    for repo_result in executor_result.get("per_repo_results") or []:
        if not isinstance(repo_result, dict):
            continue
        repo_key = canonical_plan_repo_key(str(repo_result.get("repo") or ""), discovery)
        if not repo_key:
            continue

        entry: dict[str, Any] = {
            "repo": repo_key,
            "scopes_executed": list((repo_result.get("scopes") or {}).keys()),
        }
        repo_dict = find_discovery_repo(repo_key, repos_list)
        if not repo_dict and isinstance(migration_plan, dict):
            from ado2gh.agents.migration_agent.utils import canonical_repo_id

            for plan_repo in migration_plan.get("repos") or []:
                if isinstance(plan_repo, dict) and canonical_repo_id(plan_repo) == repo_key:
                    repo_dict = plan_repo
                    break

        if not repo_dict:
            entry["discovery_missing"] = True
            findings.append(entry)
            continue

        try:
            cfg = repo_config_from_discovery(repo_dict, session)
            entry["github_org"] = cfg.gh_org
            entry["github_repo"] = cfg.gh_repo
            if not str(cfg.gh_org or "").strip():
                entry["github_org_missing"] = True
        except Exception as exc:
            entry["config_error"] = str(exc)
            findings.append(entry)
            continue

        if accel_get and entry.get("github_org") and entry.get("github_repo"):
            from ado2gh.agents.migration_agent.operator_input import parse_github_target_probe

            try:
                gh_path = f"/v1/github/repos/{entry['github_org']}/{entry['github_repo']}"
                gh_resp = await accel_get(gh_path, session_token=session_token)
                entry["github_target"] = parse_github_target_probe(
                    gh_resp if isinstance(gh_resp, dict) else None,
                )
            except Exception as exc:
                entry["github_target"] = parse_github_target_probe(error=exc)

        project = repo_dict.get("project") or (repo_key.split("/", 1)[0] if "/" in repo_key else "")
        repo_name = repo_dict.get("repo_name") or repo_dict.get("name") or ""
        if accel_get and project and repo_name:
            try:
                ado_resp = await accel_get(
                    f"/v1/ado/projects/{project}/repos/{repo_name}",
                    session_token=session_token,
                )
                entry["ado_repo"] = {
                    "id": (ado_resp or {}).get("id") if isinstance(ado_resp, dict) else None,
                    "default_branch": (ado_resp or {}).get("defaultBranch") if isinstance(ado_resp, dict) else None,
                }
            except Exception as exc:
                entry["ado_repo"] = {"error": str(exc)}

        scopes = repo_result.get("scopes") or {}
        pipe_result = scopes.get("pipelines") if isinstance(scopes.get("pipelines"), dict) else {}
        if pipe_result and pipe_result.get("status") not in ("skipped", "pending"):
            if tools_by_name.get("list_ado_pipelines") and project and repo_name:
                try:
                    ado_probe = await _invoke_validator_tool(
                        tools_by_name["list_ado_pipelines"],
                        {"project": project, "repo_name": repo_name},
                    )
                    entry["ado_pipelines_probe"] = ado_probe
                except Exception as exc:
                    entry["ado_pipelines_probe"] = {"error": str(exc)}

            ado_count = int((entry.get("ado_pipelines_probe") or {}).get("pipeline_count") or 0)

            if (
                tools_by_name.get("list_github_workflows")
                and entry.get("github_org")
                and entry.get("github_repo")
            ):
                try:
                    gh_probe = await _invoke_validator_tool(
                        tools_by_name["list_github_workflows"],
                        {
                            "github_org": entry["github_org"],
                            "github_repo": entry["github_repo"],
                        },
                    )
                    entry["github_workflows_probe"] = gh_probe
                    gh_count = int((gh_probe or {}).get("workflow_count") or 0)
                    entry["github_workflow_count"] = gh_count
                    entry["ado_pipeline_count"] = ado_count
                    if ado_count > 0 and gh_count == 0:
                        entry["pipeline_count_mismatch"] = True
                except Exception as exc:
                    entry["github_workflows_probe"] = {"error": str(exc)}

        findings.append(entry)

    session["validator_baseline_probes"] = findings
    return findings


def _build_validator_investigation_context(
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    session: dict[str, Any],
    baseline_failures: list[dict[str, Any]],
    baseline_findings: list[dict[str, Any]] | None = None,
) -> str:
    """Build human message for validator LLM investigation."""
    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )

    if dry_run:
        parts = [
            "DRY RUN — no live GitHub/ADO writes were performed.",
            "Validate using executor logs and simulated scope output below. "
            "Do not require GitHub API proof of published workflows or mirrored repos.",
            f"Dry run: true",
            f"Executor output (primary evidence): {json.dumps(
                _validator_executor_log_summary(executor_result),
                default=str,
            )[:12000]}",
        ]
    else:
        parts = [
            "LIVE RUN — verify outcomes exclusively via tool/API evidence.",
            "Do NOT treat executor status fields, scope summaries, or workflow_files "
            "from executor logs as proof of success.",
            f"Dry run: false",
            f"Executor metadata only (not evidentiary): {json.dumps(
                _validator_executor_metadata(executor_result),
                default=str,
            )[:4000]}",
        ]

    if migration_plan:
        parts.append(f"Migration plan: {json.dumps({
            'repos': migration_plan.get('repos'),
            'work_items': migration_plan.get('work_items'),
            'revision': migration_plan.get('revision'),
        }, default=str)[:6000]}")
    if baseline_findings:
        label = (
            "Dry-run executor evidence (auto)"
            if dry_run
            else "Deterministic baseline probes (auto)"
        )
        parts.append(
            f"{label}: {json.dumps(baseline_findings[:8], default=str)[:6000]}"
        )
    if baseline_failures:
        parts.append(f"Deterministic baseline failures: {json.dumps(baseline_failures[:10], default=str)}")
    profile = session.get("profile") or {}
    if isinstance(profile, dict) and profile.get("github_org"):
        parts.append(f"GitHub org: {profile.get('github_org')}")
    if _validator_has_pipeline_scope(executor_result):
        if dry_run:
            parts.append(
                "Pipeline scope ran in dry-run — review executor workflow_files and "
                "local validation stats in executor_scopes; API workflow listing is optional."
            )
        else:
            parts.append(
                f"Pipeline scope executed in live mode — perform at least "
                f"{VALIDATOR_MIN_TOOL_CALLS_PIPELINES} tool calls "
                "(list/fetch/validate workflows on GitHub) before concluding."
            )
    return "\n\n".join(parts)


async def _invoke_validator_tool(
    tool: Any,
    args: dict[str, Any],
) -> Any:
    """Invoke a LangChain validator tool (async or sync)."""
    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(args)
    if getattr(tool, "coroutine", None):
        return await tool.coroutine(**args)
    if getattr(tool, "func", None):
        return tool.func(**args)
    raise RuntimeError(f"Tool {getattr(tool, 'name', '?')} is not invokable")


async def _execute_validator_tool_calls(
    tool_calls: list[dict[str, Any]],
    tools_by_name: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for tc in tool_calls:
        name = str(tc.get("name") or "")
        args = tc.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        tool = tools_by_name.get(name)
        if not tool:
            results.append({"tool": name, "error": "unknown_tool"})
            continue
        _emit_tool_call(session, name, subagent="validator", arguments=args)
        try:
            out = await _invoke_validator_tool(tool, args)
            entry = {"tool": name, "result": out}
            results.append(entry)
            _emit_tool_result(session, name, entry, subagent="validator")
        except Exception as exc:
            entry = {"tool": name, "error": str(exc)}
            results.append(entry)
            _emit_tool_result(session, name, entry, subagent="validator")
    return results


def _normalize_validator_failure(raw: Any, *, default_repo: str = "") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    specific = str(
        raw.get("specific_failure")
        or raw.get("failure")
        or raw.get("error")
        or ""
    ).strip()
    if not specific:
        return None
    return {
        "repo": raw.get("repo") or default_repo,
        "scope": raw.get("scope") or "",
        "expected_state": raw.get("expected_state"),
        "observed_state": raw.get("observed_state"),
        "specific_failure": specific,
        "file_path": raw.get("file_path"),
        "recommended_remediation": raw.get("recommended_remediation") or raw.get("remediation"),
        "error": specific,
        "source": raw.get("source") or "validator_llm",
        "operator_input_required": raw.get("operator_input_required"),
    }


async def _run_validator_llm_investigation(
    state: dict[str, Any],
    *,
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any] | None,
    baseline_failures: list[dict[str, Any]],
    baseline_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Multi-round LLM validation with read-only tools."""
    llm = state.get("llm")
    if not llm or state.get("llm_unconfigured"):
        return None

    session = state.get("session") or {}
    accel_get = state.get("accel_get")
    session_token = state.get("session_token")
    capabilities = state.get("capabilities")

    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )

    try:
        from ado2gh.agents.migration_agent.tools import get_validator_tools

        validator_tools = get_validator_tools(
            accel_get,
            session_token,
            session_getter=lambda: session,
        )
    except Exception:
        return None

    if not validator_tools:
        return None

    tools_by_name = {t.name: t for t in validator_tools if getattr(t, "name", None)}
    system_prompt = get_prompt("validator")
    context = _build_validator_investigation_context(
        executor_result,
        migration_plan,
        session,
        baseline_failures,
        baseline_findings=baseline_findings,
    )

    messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    llm_with_tools = llm
    if capabilities and getattr(capabilities, "supports_tool_calling", False):
        try:
            llm_with_tools = llm.bind_tools(validator_tools)
        except Exception:
            pass

    parsed_report: dict[str, Any] | None = None
    tool_calls_total = 0
    seen_thinking: set[str] = set()
    pipeline_scope = _validator_has_pipeline_scope(executor_result)
    min_tool_calls = (
        0
        if dry_run
        else (VALIDATOR_MIN_TOOL_CALLS_PIPELINES if pipeline_scope else 1)
    )

    for round_idx in range(VALIDATOR_MAX_TOOL_ROUNDS):
        _append_and_stream(
            session,
            role="system",
            content=f"Validator: evidence gathering round {round_idx + 1}/{VALIDATOR_MAX_TOOL_ROUNDS}…",
            subagent="validator",
        )
        response_text = await _stream_llm_response(
            llm_with_tools,
            messages,
            state,
            subagent="validator",
            capabilities=capabilities,
        )
        parsed = _parse_llm_json(response_text) or {}
        _validator_append_thinking(session, parsed.get("thinking"), seen_thinking)

        if parsed.get("operator_input_request"):
            return {"operator_input_request": parsed["operator_input_request"]}

        report = parsed.get("validation_report")
        if isinstance(report, dict):
            parsed_report = report
            break
        if parsed.get("passed") is not None and not parsed.get("tool_calls"):
            parsed_report = parsed
            break

        tool_calls = parsed.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        if not tool_calls:
            if parsed.get("failures") or parsed.get("analysis"):
                parsed_report = parsed
                break
            if (
                tool_calls_total >= min_tool_calls or round_idx >= 1
            ) and _validator_text_indicates_blocker(parsed):
                parsed_report = {
                    "passed": False,
                    "analysis": str(parsed.get("thinking") or parsed.get("analysis") or "").strip(),
                    "failures": parsed.get("failures")
                    or [{
                        "scope": "validation",
                        "specific_failure": str(parsed.get("thinking") or "Validator could not verify migration outcomes."),
                        "operator_input_required": True,
                        "source": "validator_llm",
                    }],
                }
                break
            if round_idx >= VALIDATOR_MAX_TOOL_ROUNDS - 1:
                break
            messages.append(AIMessage(content=response_text or "{}"))
            if tool_calls_total >= min_tool_calls:
                messages.append(
                    HumanMessage(
                        content=(
                            "Evidence gathering is sufficient. If validation cannot complete, "
                            "respond with operator_input_request JSON (not repeated thinking). "
                            "Otherwise emit validation_report with tool-backed evidence."
                        )
                    )
                )
            else:
                nudge = (
                    "Summarize validation from executor logs and simulated scope output. "
                    "API tool_calls are optional in dry-run."
                    if dry_run
                    else (
                        "Use tool_calls to verify ADO/GitHub state independently. "
                        "Do not conclude from executor status or logs alone."
                    )
                )
                messages.append(
                    HumanMessage(content=nudge)
                )
            continue

        tool_calls_total += len(tool_calls)
        tool_results = await _execute_validator_tool_calls(tool_calls, tools_by_name, session)
        messages.append(AIMessage(content=response_text))
        messages.append(
            HumanMessage(
                content=(
                    "Tool results (continue investigation or emit validation_report when done):\n"
                    + json.dumps(
                        {
                            "tool_results": tool_results,
                            "tool_calls_total": tool_calls_total,
                            "min_required": min_tool_calls,
                        },
                        default=str,
                    )[:12000]
                ),
            ),
        )

    if parsed_report is None:
        return None

    parsed_report.setdefault("tool_calls_total", tool_calls_total)
    if pipeline_scope and not dry_run:
        if tool_calls_total < VALIDATOR_MIN_TOOL_CALLS_PIPELINES and parsed_report.get("passed", True):
            parsed_report["passed"] = False
            parsed_report.setdefault("analysis", "")
            parsed_report["analysis"] += (
                f" Insufficient tool evidence ({tool_calls_total} calls; "
                f"need {VALIDATOR_MIN_TOOL_CALLS_PIPELINES}+ for pipeline validation)."
            )
            parsed_report.setdefault("failures", []).append({
                "scope": "pipelines",
                "specific_failure": "Validator did not complete required API/tool checks for pipelines",
                "operator_input_required": True,
                "recommended_remediation": (
                    "Re-run validation with list_ado_pipelines, list_github_workflows, "
                    "fetch_github_workflow, and validate_workflow_syntax"
                ),
            })
    return parsed_report


async def validator_node(state: dict[str, Any]) -> dict[str, Any]:
    """Validator node — verifies migration outcomes using evidence-based checks.

    Binds validator tools, streams validation thoughts, verifies migration
    outcomes via API calls and local validation, produces ValidationResult
    with per-scope pass/fail, sets AgentState.validation_feedback for failures,
    validates no operations outside approved plan.
    """
    session = state.get("session") or {}
    _transition_session(session, SessionState.VALIDATING)
    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    capabilities = state.get("capabilities")
    accel_get = state.get("accel_get")
    session_token = state.get("session_token")
    migration_plan = state.get("migration_plan") or session.get("migration_plan")
    executor_result = state.get("executor_result")
    iteration = state.get("iteration", 0)
    pev_retry_count = state.get("pev_retry_count", 0)

    iteration += 1

    from ado2gh.agents.migration_agent.utils import resolve_dry_run

    dry_run_preview = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    ) if executor_result else bool(session.get("dry_run", True))

    _append_and_stream(
        session,
        role="system",
        content=(
            "Validator: starting dry-run validation (executor logs)…"
            if dry_run_preview
            else "Validator: starting live validation (API/tool evidence)…"
        ),
        subagent="validator",
    )

    if not executor_result:
        _append_and_stream(
            session,
            role="system",
            content="Validator: no executor result — nothing to validate.",
            subagent="validator",
        )
        return {
            "validation_result": {"passed": False, "per_scope": {}, "failures": ["no_executor_result"]},
            "validation_feedback": {"failures": ["no_executor_result"]},
            "iteration": iteration,
            "should_return": False,
        }

    per_repo_results = executor_result.get("per_repo_results", [])
    failures = executor_result.get("failures", [])

    dry_run = resolve_dry_run(
        session,
        migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
        executor_result=executor_result,
    )

    # T104: Bind validator tools to LLM for tool-calling
    validator_tools = None
    if llm and not llm_unconfigured and capabilities and capabilities.supports_tool_calling:
        try:
            from ado2gh.agents.migration_agent.tools import get_validator_tools
            validator_tools = get_validator_tools(
                accel_get,
                session_token,
                session_getter=lambda: session,
            )
        except Exception:
            pass

    # Bind tools to LLM if tool calling is supported
    llm_with_tools = llm
    if capabilities and capabilities.supports_tool_calling and validator_tools:
        try:
            llm_with_tools = llm.bind_tools(validator_tools)
        except Exception:
            pass

    # Per-scope validation
    per_scope: dict[str, dict[str, Any]] = {}
    all_failures: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    for repo_result in per_repo_results:
        repo_id = repo_result.get("repo", "")
        scopes = repo_result.get("scopes", {})

        for scope_name, scope_result in scopes.items():
            if not dry_run:
                if scope_result.get("error"):
                    all_failures.append({
                        "repo": repo_id,
                        "scope": scope_name,
                        "specific_failure": scope_result["error"],
                        "error": scope_result["error"],
                        "recommended_remediation": f"Fix error in {scope_name} execution and retry",
                        "source": "executor_error",
                    })
                continue

            scope_validation = _validate_scope(scope_name, scope_result, migration_plan, dry_run)
            per_scope.setdefault(scope_name, {"repos": []})
            per_scope[scope_name]["repos"].append({
                "repo": repo_id,
                "passed": scope_validation["passed"],
                "evidence": scope_validation.get("evidence"),
            })

            if not scope_validation["passed"]:
                failure_entry = {
                    "repo": repo_id,
                    "scope": scope_name,
                    "expected_state": scope_validation.get("expected_state"),
                    "observed_state": scope_validation.get("observed_state"),
                    "specific_failure": scope_validation.get("failure"),
                    "recommended_remediation": scope_validation.get("remediation"),
                    "error": scope_validation.get("failure"),
                }
                from ado2gh.agents.migration_agent.operator_input import is_fr036_failure

                if is_fr036_failure(failure_entry):
                    failure_entry["error_code"] = "migration_in_progress"
                    failure_entry["operator_input_required"] = True
                all_failures.append(failure_entry)

            if scope_validation.get("evidence"):
                evidence.append(scope_validation["evidence"])

    # Check for plan-vs-execution consistency
    discovery = session.get("discovery_snapshot")
    if isinstance(discovery, str):
        import json
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    if not isinstance(discovery, dict):
        discovery = {}

    from ado2gh.agents.migration_agent.utils import (
        canonical_plan_repo_key,
        plan_repo_key_aliases,
        repo_matches_plan_keys,
    )

    plan_aliases = plan_repo_key_aliases(migration_plan, discovery)
    extra_repos: set[str] = set()
    for repo_result in per_repo_results:
        executed = canonical_plan_repo_key(str(repo_result.get("repo", "") or ""), discovery)
        if not executed:
            continue
        if not repo_matches_plan_keys(executed, plan_aliases, discovery):
            extra_repos.add(executed)
    if extra_repos:
        all_failures.append({
            "scope": "plan_consistency",
            "specific_failure": f"Executor processed repos not in plan: {extra_repos}",
            "recommended_remediation": "Review executor output and ensure only planned repos are processed",
        })

    # Include executor failures in validation (ignore benign dry-run infrastructure skips)
    from ado2gh.agents.migration_agent.operator_input import is_fr036_failure

    for f in failures:
        if _failure_is_benign(f, dry_run=dry_run):
            continue
        failure_entry = {
            "repo": f.get("repo", ""),
            "scope": f.get("scope", ""),
            "error": f.get("error", ""),
            "specific_failure": f.get("specific_failure") or f.get("error", "Execution error"),
            "error_code": f.get("error_code", "execution_error"),
            "lock_holder_session_id": f.get("lock_holder_session_id"),
            "holder_run_id": f.get("holder_run_id"),
            "operator_input_required": f.get("operator_input_required"),
        }
        if is_fr036_failure(failure_entry):
            failure_entry["error_code"] = "migration_in_progress"
            failure_entry["operator_input_required"] = True
        if is_fr036_failure(failure_entry):
            failure_entry["error_code"] = "migration_in_progress"
            failure_entry["operator_input_required"] = True
        all_failures.append(failure_entry)

    # Deterministic baseline probes before LLM investigation
    baseline_findings: list[dict[str, Any]] = []
    if per_repo_results:
        try:
            baseline_findings = await _gather_validator_baseline_probes(
                session,
                executor_result,
                migration_plan if isinstance(migration_plan, dict) else None,
                accel_get,
                session_token,
            )
        except Exception as exc:
            _append_and_stream(
                session,
                role="system",
                content=f"Validator: baseline probe error — {exc}",
                subagent="validator",
            )
        else:
            from ado2gh.agents.migration_agent.operator_input import (
                validation_failures_from_baseline_probes,
            )

            probe_failures = validation_failures_from_baseline_probes(baseline_findings)
            if probe_failures:
                all_failures.extend(probe_failures)
                _append_and_stream(
                    session,
                    role="system",
                    content=(
                        f"Validator: baseline probes found {len(probe_failures)} issue(s) — "
                        "including in validation report."
                    ),
                    subagent="validator",
                )

    # LLM-driven evidence validation (APIs + local workflow checks)
    llm_analysis: str | None = None
    if llm and not llm_unconfigured and accel_get:
        try:
            llm_report = await _run_validator_llm_investigation(
                state,
                executor_result=executor_result,
                migration_plan=migration_plan if isinstance(migration_plan, dict) else None,
                baseline_failures=list(all_failures),
                baseline_findings=baseline_findings,
            )
        except Exception as exc:
            llm_report = None
            _append_and_stream(
                session,
                role="system",
                content=f"Validator: LLM investigation error — {exc}",
                subagent="validator",
            )
        else:
            if isinstance(llm_report, dict) and llm_report.get("operator_input_request"):
                from ado2gh.agents.migration_agent.operator_input import (
                    blockers_from_baseline_probes,
                    blockers_from_validator_baseline_probes,
                    operator_input_from_probe_failures,
                    store_operator_input,
                )
                from ado2gh.agents.migration_agent.operator_input_schema import OperatorInputRequest

                op_raw = llm_report["operator_input_request"]
                op_req: OperatorInputRequest | None = None
                try:
                    op_req = OperatorInputRequest.model_validate(op_raw)
                except Exception:
                    blocker_list = (
                        blockers_from_baseline_probes(baseline_findings)
                        + blockers_from_validator_baseline_probes(baseline_findings)
                    )
                    if not blocker_list:
                        blocker_list = [{
                            "repo": str(per_repo_results[0].get("repo") if per_repo_results else ""),
                            "scope": "validation",
                            "blocker": str(op_raw.get("description") or op_raw.get("thinking") or "Validator needs operator decision."),
                            "key": "validation:llm_operator_request",
                        }]
                    op_req = operator_input_from_probe_failures(
                        blocker_list, session, source="validator",
                    )
                if op_req:
                    store_operator_input(session, op_req)
                    all_failures.append({
                        "scope": "validation",
                        "specific_failure": op_req.description[:500],
                        "operator_input_required": True,
                        "source": "validator_llm",
                    })
                    _append_and_stream(
                        session,
                        role="system",
                        content="Validator: operator decision required — escalating to orchestrator.",
                        subagent="validator",
                    )
            elif isinstance(llm_report, dict):
                llm_analysis = str(llm_report.get("analysis") or "").strip() or None
                if llm_report.get("per_scope"):
                    for scope_name, scope_data in llm_report["per_scope"].items():
                        if not isinstance(scope_data, dict):
                            continue
                        per_scope.setdefault(scope_name, {"repos": []})
                        for repo_row in scope_data.get("repos") or []:
                            if isinstance(repo_row, dict):
                                per_scope[scope_name]["repos"].append(repo_row)
                if llm_report.get("passed") is False:
                    added = False
                    for raw_failure in llm_report.get("failures") or []:
                        normalized = _normalize_validator_failure(raw_failure)
                        if normalized:
                            all_failures.append(normalized)
                            added = True
                    if not added and llm_analysis:
                        all_failures.append({
                            "scope": "validation",
                            "specific_failure": llm_analysis[:1000],
                            "recommended_remediation": "Revise the migration plan using validator analysis",
                            "source": "validator_llm",
                            "error": llm_analysis[:500],
                        })
                if llm_analysis:
                    evidence.append({"llm_analysis": llm_analysis, "tool_calls": llm_report.get("tool_calls_total")})
                    _append_and_stream(
                        session,
                        role="system",
                        content=f"Validator: analysis complete — {llm_analysis[:500]}",
                        subagent="validator",
                    )

    passed = len(all_failures) == 0
    validation_result = {
        "passed": passed,
        "per_scope": per_scope,
        "evidence": evidence,
        "failures": all_failures,
        "dry_run": dry_run,
        "llm_analysis": llm_analysis,
    }

    # T100: Advance queue index after executor consumed a queue slot (even if no scopes ran).
    migration_queue = state.get("migration_queue")
    if passed and migration_queue:
        current_index = int(migration_queue.get("current_index", 0) or 0)
        queue_items = migration_queue.get("items", [])
        current_repo_id = str((executor_result or {}).get("current_repo_id", "") or "").strip()
        if not current_repo_id and per_repo_results:
            current_repo_id = str(per_repo_results[0].get("repo", "") or "").strip()

        if current_index < len(queue_items):
            _advance_migration_queue(migration_queue, repo_id=current_repo_id)
            _append_and_stream(
                session,
                role="system",
                content=(
                    f"Validator: validation passed for {current_repo_id or 'repo'} — "
                    f"queue advanced to {migration_queue['current_index']}/{len(queue_items)}"
                ),
                subagent="validator",
            )
        else:
            _append_and_stream(
                session,
                role="system",
                content="Validator: queue already complete — no further repos to validate.",
                subagent="validator",
            )

    # Set validation feedback for failures (triggers planner retry unless non-retryable)
    from ado2gh.agents.migration_agent.operator_input import failures_require_operator_escalation

    validation_feedback = None
    pending_operator_input_payload: dict[str, Any] | None = None
    pending_clarification: dict[str, Any] | None = None
    operator_escalation = not passed and failures_require_operator_escalation(all_failures)
    if operator_escalation:
        from ado2gh.agents.migration_agent.operator_input import (
            blockers_from_baseline_probes,
            blockers_from_validator_baseline_probes,
            operator_input_from_probe_failures,
            operator_input_from_validator_failures,
            store_operator_input,
        )

        op_req = operator_input_from_validator_failures(
            all_failures, session, plan=migration_plan if isinstance(migration_plan, dict) else None,
        )
        if op_req is None and baseline_findings:
            probe_blockers = (
                blockers_from_baseline_probes(baseline_findings)
                + blockers_from_validator_baseline_probes(baseline_findings)
            )
            if probe_blockers:
                op_req = operator_input_from_probe_failures(
                    probe_blockers, session, source="validator",
                )
        if op_req is None:
            from ado2gh.agents.migration_agent.operator_input import pending_operator_input

            op_req = pending_operator_input(session)
        if op_req is not None and hasattr(op_req, "model_dump"):
            store_operator_input(session, op_req)
            pending_operator_input_payload = op_req.model_dump()
            pending_clarification = {
                "message_type": "operator_input_required",
                "payload": pending_operator_input_payload,
            }
        elif isinstance(op_req, dict):
            pending_operator_input_payload = op_req
            pending_clarification = {
                "message_type": "operator_input_required",
                "payload": op_req,
            }

        validation_feedback = {
            "failures": all_failures,
            "retry_recommended": False,
            "escalate": True,
            "retry_count": pev_retry_count,
        }
        session["pev_max_retries_exhausted"] = True
        session.pop("start_execution", None)
        _append_and_stream(
            session,
            role="system",
            content="Validator: non-retryable failure — escalating to orchestrator.",
            subagent="validator",
        )
    elif not passed and pev_retry_count < MAX_PEV_RETRIES:
        validation_feedback = {
            "failures": all_failures,
            "analysis": llm_analysis,
            "retry_recommended": True,
            "retry_count": pev_retry_count + 1,
        }
        pev_retry_count += 1
        _append_and_stream(
            session,
            role="system",
            content=f"Validator: validation failed — sending feedback to planner (retry {pev_retry_count}/{MAX_PEV_RETRIES}).",
            subagent="validator",
        )
    elif not passed:
        validation_feedback = {
            "failures": all_failures,
            "analysis": llm_analysis,
            "retry_recommended": False,
            "escalate": True,
            "retry_count": pev_retry_count,
        }
        session["pev_max_retries_exhausted"] = True
        session.pop("start_execution", None)
        _append_and_stream(
            session,
            role="system",
            content="Validator: validation failed — max retries exhausted, sending to planner.",
            subagent="validator",
        )
    else:
        _append_and_stream(
            session,
            role="system",
            content="Validator: all checks passed — sending results to planner.",
            subagent="validator",
        )

    # Determine next action for cycle summary
    if passed:
        next_action = "complete"
    elif pev_retry_count >= MAX_PEV_RETRIES:
        next_action = "fail_max_retries"
    elif iteration >= state.get("max_iterations", 20):
        next_action = "fail_max_iterations"
    else:
        next_action = "retry_planner"

    # T054: Generate PevCycleSummary
    cycle_number = pev_retry_count + 1 if not passed else pev_retry_count
    cycle_summary = _make_cycle_summary(
        cycle_number=cycle_number,
        executor_result=executor_result,
        validation_result=validation_result,
        next_action=next_action,
    )

    # T052: Create inter-agent message from validator
    inter_agent_msg = _make_inter_agent_message(
        from_role="validator",
        to_role="planner",
        message_type="validation_result",
        payload={
            "passed": passed,
            "failures": all_failures[:5],
            "analysis": (llm_analysis or "")[:2000],
            "retry_count": pev_retry_count,
        },
    )

    return {
        "validation_result": validation_result,
        "validation_feedback": validation_feedback,
        "pev_retry_count": pev_retry_count,
        "iteration": iteration,
        **({"migration_queue": migration_queue} if migration_queue is not None else {}),
        **({"pending_operator_input": pending_operator_input_payload} if pending_operator_input_payload else {}),
        **({"pending_clarification": pending_clarification} if pending_clarification else {}),
        "should_return": False,
        "cycle_summaries": [cycle_summary],
        "inter_agent_messages": [inter_agent_msg],
    }


def _failure_text(failure: Any) -> str:
    if not isinstance(failure, dict):
        return str(failure).lower()
    return " ".join(
        str(failure.get(key, ""))
        for key in (
            "specific_failure",
            "error",
            "failure",
            "recommended_remediation",
            "remediation",
            "message",
        )
    ).lower()


_BENIGN_DRY_RUN_HINTS = (
    "endpoint unavailable",
    "accelerator endpoint unavailable",
    "accelerator_unavailable",
    "not included in migration scopes",
    "no operator secret mappings",
)


_DRY_RUN_SCOPE_OK_STATUSES = frozenset({
    "skipped", "success", "simulated", "pending", "dry_run",
})


def _failure_is_benign(failure: Any, *, dry_run: bool) -> bool:
    if not dry_run:
        return False
    text = _failure_text(failure)
    if "404" in text or "not found" in text:
        return True
    return any(hint in text for hint in _BENIGN_DRY_RUN_HINTS)


def _all_failures_benign(failures: list[Any], *, dry_run: bool) -> bool:
    return bool(failures) and all(_failure_is_benign(f, dry_run=dry_run) for f in failures)


def _scope_result_is_benign(scope_result: dict[str, Any], *, dry_run: bool) -> bool:
    status = scope_result.get("status")
    if status in ("skipped", "pending"):
        return True
    if dry_run:
        message = str(
            scope_result.get("message") or scope_result.get("detail") or ""
        ).lower()
        if any(hint in message for hint in _BENIGN_DRY_RUN_HINTS):
            return True
    return False


def _validate_scope(
    scope: str,
    scope_result: dict[str, Any],
    plan: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Validate a single scope's execution result."""
    if _scope_result_is_benign(scope_result, dry_run=dry_run):
        return {
            "passed": True,
            "evidence": {
                "scope": scope,
                "status": scope_result.get("status", "skipped"),
            },
        }

    if scope_result.get("error"):
        return {
            "passed": False,
            "failure": scope_result["error"],
            "observed_state": "error",
            "expected_state": "success",
            "remediation": f"Fix error in {scope} execution and retry",
        }

    if scope_result.get("status") == "skipped":
        return {
            "passed": True,
            "evidence": {"scope": scope, "status": "skipped"},
        }

    if scope_result.get("status") == "pending":
        return {
            "passed": True,
            "evidence": {"scope": scope, "status": "pending"},
        }

    # Scope-specific validation logic
    if scope in ("repo", "git"):
        status = scope_result.get("status", "unknown")
        if dry_run:
            if status in _DRY_RUN_SCOPE_OK_STATUSES:
                return {"passed": True, "evidence": {"scope": scope, "dry_run": True, "status": status}}
            return {
                "passed": False,
                "failure": f"Unexpected git scope status in dry-run: {status}",
                "observed_state": status,
                "expected_state": "skipped, success, or dry_run",
                "remediation": "Review executor dry-run output for the git scope",
            }
        # For live, check for SHA parity (would use validate_git tool)
        return {"passed": True, "evidence": {"scope": "git", "status": status}}

    if scope == "pipelines":
        status = scope_result.get("status", "unknown")
        if dry_run and status not in _DRY_RUN_SCOPE_OK_STATUSES:
            return {
                "passed": False,
                "failure": f"Unexpected pipelines scope status in dry-run: {status}",
                "observed_state": status,
                "expected_state": "skipped, success, or dry_run",
                "remediation": "Review executor dry-run output for the pipelines scope",
            }
        validation_failed = int(scope_result.get("validation_failed") or 0)
        validation_errors = scope_result.get("validation_errors") or []
        if validation_failed > 0 or validation_errors:
            return {
                "passed": False,
                "failure": validation_errors[0] if validation_errors else "Pipeline YAML validation failed",
                "observed_state": {
                    "validation_failed": validation_failed,
                    "validation_errors": validation_errors[:5],
                },
                "expected_state": "All converted workflows pass local validation",
                "remediation": (
                    "Planner should revise pipeline conversion: fix triggers, runs-on, steps, "
                    "or unsupported ADO tasks before re-execution"
                ),
                "evidence": {
                    "scope": "pipelines",
                    "validation_failed": validation_failed,
                    "validation_errors": validation_errors[:10],
                    "workflow_files": scope_result.get("workflow_files") or [],
                },
            }
        completed = int(scope_result.get("completed") or 0)
        failed = int(scope_result.get("failed") or 0)
        if not dry_run and failed > 0:
            return {
                "passed": False,
                "failure": scope_result.get("message") or f"{failed} pipeline(s) failed conversion",
                "observed_state": {"completed": completed, "failed": failed},
                "expected_state": "All planned pipelines converted",
                "remediation": "Review failed pipeline transforms and update the plan",
            }
        return {
            "passed": True,
            "evidence": {
                "scope": "pipelines",
                "status": status,
                "completed": completed,
                "validation_passed": scope_result.get("validation_passed"),
                "workflow_files": scope_result.get("workflow_files") or [],
            },
        }

    # Default: pass if no error
    return {"passed": True, "evidence": {"scope": scope, "status": scope_result.get("status", "success")}}


# ─── Orchestrator tool execution (inlined — no separate graph node) ───

def _planner_handoff_state_clear() -> dict[str, Any]:
    """Drop stale graph plan/PEV fields when invoke_planner requests a fresh plan."""
    return {
        "migration_plan": None,
        "migration_queue": None,
        "executor_result": None,
        "validation_result": None,
        "validation_feedback": None,
        "pending_clarification": None,
        "pending_operator_input": None,
    }


async def _apply_orchestrator_tools(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Run orchestrator tool calls inline before graph routing."""
    tool_calls = result.get("tool_calls") or []
    if not tool_calls or result.get("should_return"):
        return result
    merged_state = {**state, **result}
    tool_out = await _execute_orchestrator_tools(merged_state, tool_calls)
    result = {**result, **tool_out, "tool_calls": []}
    if tool_out.get("pending_form"):
        result["should_return"] = True
    return result


async def _execute_orchestrator_tools(
    state: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute orchestrator tool calls (forms, planner handoff, read-only queries)."""
    session = state.get("session") or {}
    accel_get = state.get("accel_get")
    accel_post = state.get("accel_post")
    build_plan = state.get("build_plan")
    session_token = state.get("session_token")

    results = []
    start_pev = False
    pending_form = None
    should_return = False
    reply = None
    planner_handoff_clear: dict[str, Any] = {}

    for tc in tool_calls:
        tool_name = tc.get("name", "")
        args = tc.get("arguments", {}) or {}
        if not isinstance(args, dict):
            args = {}

        is_cached_discovery = (
            tool_name == "call_accelerator"
            and "/discovery" in str(args.get("endpoint", ""))
            and session.get("discovery_snapshot")
        )
        if not is_cached_discovery:
            _emit_tool_call(session, tool_name, subagent="orchestrator", arguments=args)

        if tool_name == "get_current_profile":
            from ado2gh.agents.migration_agent.tools.shared_tools import fetch_current_profile

            try:
                result = await fetch_current_profile(
                    accel_get,
                    session_token=session_token,
                    session_getter=lambda: session,
                )
                results.append({"tool": tool_name, "result": result})
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
        elif tool_name == "ado_api_query" and accel_get:
            try:
                endpoint = str(args.get("endpoint", "")).lstrip("/")
                result = await accel_get(f"/v1/ado/{endpoint}", session_token=session_token)
                results.append({"tool": tool_name, "result": result})
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
        elif tool_name == "github_api_query" and accel_get:
            try:
                endpoint = str(args.get("endpoint", "")).lstrip("/")
                result = await accel_get(f"/v1/github/{endpoint}", session_token=session_token)
                results.append({"tool": tool_name, "result": result})
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
        elif tool_name == "generate_plan" and build_plan:
            try:
                phase = args.get("phase") or session.get("plan_phase")
                repo_id = args.get("repository_id") or session.get("plan_repository_id")
                result = await build_plan(
                    session,
                    session_token,
                    phase=phase,
                    repository_id=repo_id,
                )
                if result and result.get("error"):
                    results.append({"tool": tool_name, "error": result.get("message", str(result.get("error")))})
                    state["messages"] = state.get("messages", []) + [{
                        "role": "tool",
                        "content": f"Tool '{tool_name}' failed: {result.get('message', '')}",
                    }]
                else:
                    session["migration_plan"] = result
                    results.append({"tool": tool_name, "result": result})
                    state["messages"] = state.get("messages", []) + [{
                        "role": "tool",
                        "content": f"Tool '{tool_name}' succeeded. Plan built with {len(result.get('work_items', []))} work items.",
                    }]
            except Exception as e:
                results.append({"tool": tool_name, "error": str(e)})
                state["messages"] = state.get("messages", []) + [{
                    "role": "tool",
                    "content": f"Tool '{tool_name}' raised exception: {e}",
                }]
        elif tool_name == "invoke_planner":
            from ado2gh.agents.migration_agent.intake import (
                analysis_from_state,
                apply_message_analysis,
                intake_from_session,
                intake_ready_for_planner,
                resolve_intake_routing,
                sync_intake_to_session,
            )
            from ado2gh.agents.migration_agent.intake_guardrails import (
                apply_analysis_guardrails,
                repository_confirmed_for_turn,
            )

            user_message = state.get("user_message", "") or ""
            intake = intake_from_session(session)
            analysis = analysis_from_state(state)
            if analysis:
                analysis = apply_analysis_guardrails(analysis, user_message)
                intake = apply_message_analysis(intake, analysis)
            repo_for_form = (
                args.get("repository_id")
                or intake.resolved_repository_id()
                or session.get("plan_repository_id", "")
            )
            if repo_for_form and not intake.resolved_repository_id():
                intake = intake.model_copy(update={"repository_id": repo_for_form})
            sync_intake_to_session(session, intake)
            if not intake_ready_for_planner(intake):
                routing = await resolve_intake_routing(
                    state,
                    session,
                    intent="migration_action",
                    user_message=user_message,
                    analysis=analysis,
                )
                if routing.get("action") == "request_form":
                    pending_form = routing["form"]
                    results.append({
                        "tool": tool_name,
                        "result": {"status": "awaiting_intake", "missing": routing.get("missing_fields")},
                    })
                    if not is_cached_discovery:
                        _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")
                    continue
            repo_id = (
                args.get("repository_id")
                or intake.resolved_repository_id()
                or session.get("plan_repository_id", "")
            )
            if not repository_confirmed_for_turn(repo_id, analysis, session=session):
                routing = await resolve_intake_routing(
                    state,
                    session,
                    intent="migration_action",
                    user_message=user_message,
                    analysis=analysis,
                )
                if routing.get("action") == "request_form":
                    pending_form = routing["form"]
                results.append({
                    "tool": tool_name,
                    "result": {
                        "status": "awaiting_intake",
                        "error": "repository_not_named_in_message",
                        "missing": routing.get("missing_fields") if routing else ["repository_id"],
                    },
                })
                if not is_cached_discovery:
                    _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")
                continue
            if repo_id and "/" not in repo_id:
                from ado2gh.agents.migration_agent.utils import find_discovery_repo, canonical_repo_id

                discovery = session.get("discovery_snapshot") or {}
                repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
                match = find_discovery_repo(repo_id, repos)
                if match:
                    repo_id = canonical_repo_id(match)
            dry_run = session.get("dry_run", args.get("dry_run", True))
            phase = args.get("phase")
            session["plan_repository_id"] = repo_id
            session["dry_run"] = dry_run
            if phase:
                session["plan_phase"] = phase
            _begin_new_agent_migration(session, repository_id=repo_id)
            start_pev = True
            planner_handoff_clear = _planner_handoff_state_clear()
            results.append({"tool": tool_name, "result": {
                "status": "invoking_planner",
                "repository_id": repo_id,
                "dry_run": dry_run,
            }})
        elif tool_name == "invoke_bulk_planner":
            from ado2gh.agents.migration_agent.intake import (
                analysis_from_state,
                intake_from_session,
                intake_ready_for_planner,
                resolve_intake_routing,
            )

            intake = intake_from_session(session)
            if not intake_ready_for_planner(intake):
                routing = await resolve_intake_routing(
                    state,
                    session,
                    intent="migration_action",
                    user_message=state.get("user_message", "") or "",
                    analysis=analysis_from_state(state),
                )
                if routing.get("action") == "request_form":
                    pending_form = routing["form"]
                    results.append({
                        "tool": tool_name,
                        "result": {"status": "awaiting_intake", "missing": routing.get("missing_fields")},
                    })
                    if not is_cached_discovery:
                        _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")
                    continue
            # Orchestrator is handing off to the Planner for bulk migration
            repo_ids = args.get("repository_ids", [])
            if not repo_ids:
                # Fallback: extract repo IDs from user message
                user_msg = state.get("user_message", "") or ""
                import re as _re
                repo_ids = _re.findall(r'([\w.-]+/[\w.-]+)', user_msg)
            # Prefer session's dry_run over LLM's argument
            dry_run = session.get("dry_run", args.get("dry_run", True))
            phase = args.get("phase")
            session["plan_repository_ids"] = repo_ids
            session["dry_run"] = dry_run
            if phase:
                session["plan_phase"] = phase
            _begin_new_agent_migration(session)
            start_pev = True
            planner_handoff_clear = _planner_handoff_state_clear()
            results.append({"tool": tool_name, "result": {
                "status": "invoking_bulk_planner",
                "repository_ids": repo_ids,
                "dry_run": dry_run,
            }})
        elif tool_name == "run_migration_pev":
            _begin_new_agent_migration(session)
            start_pev = True
            planner_handoff_clear = _planner_handoff_state_clear()
            results.append({"tool": tool_name, "result": {"status": "pev_starting"}})
        elif tool_name == "fetch_migration_status" and accel_get:
            run_id = session.get("run_id")
            if run_id and accel_get:
                try:
                    result = await accel_get(
                        f"/v1/pipeline/runs/{run_id}",
                        session_token=session_token,
                    )
                    results.append({"tool": tool_name, "result": result})
                except Exception as e:
                    results.append({"tool": tool_name, "error": str(e)})
            else:
                results.append({"tool": tool_name, "result": {"status": "no_active_run"}})
        elif tool_name == "request_user_input":
            from ado2gh.agents.migration_agent.forms import sanitize_form
            # Apply guardrails to LLM-generated form (truncation, field limits)
            form_args = sanitize_form(dict(args))
            pending_form = form_args
            results.append({"tool": tool_name, "result": {"form": form_args}})
        else:
            results.append({"tool": tool_name, "error": f"Unknown tool: {tool_name}"})

        if not is_cached_discovery and results and results[-1].get("tool") == tool_name:
            _emit_tool_result(session, tool_name, results[-1], subagent="orchestrator")

    # Process queued messages after tool execution
    if _has_queued_messages(session):
        queued = _drain_message_queue(session)
        for msg in queued:
            _append_event(session, role="user", content=msg, kind="message")

    return {
        "start_pev": start_pev,
        "pending_form": pending_form,
        "should_return": should_return or bool(pending_form),
        "reply": reply,
        **planner_handoff_clear,
    }


async def execute_tools_node(state: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias — tools run inside orchestrator_node."""
    return await _execute_orchestrator_tools(state, state.get("tool_calls") or [])


# ─── Node: finalize ───────────────────────────────────────────────────

async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Prepare response and append events to session."""
    session = state.get("session") or {}
    reply = state.get("reply")

    # Generate a summary reply if validation passed but no reply was set
    if not reply:
        validation = state.get("validation_result") or {}
        executor_result = state.get("executor_result") or {}
        if validation.get("passed"):
            from ado2gh.agents.migration_agent.message_format import compose_migration_completion_message

            plan = state.get("migration_plan") or session.get("migration_plan") or {}
            if isinstance(plan, str):
                try:
                    import json as _json
                    plan = _json.loads(plan)
                except Exception:
                    plan = {}
            reply = await compose_migration_completion_message(
                state,
                session,
                validation_result=validation,
                executor_result=executor_result,
                migration_plan=plan if isinstance(plan, dict) else {},
            )
            _append_and_stream(
                session,
                role="system",
                content="Orchestrator: migration complete — summary sent to operator.",
                subagent="orchestrator",
            )
        elif state.get("error"):
            reply = await compose_orchestrator_chat_message(
                state,
                session,
                instruction="Explain the migration failure to the operator.",
                context={"error": state.get("error"), "validation_result": validation},
            )
        elif validation.get("failures"):
            from ado2gh.agents.migration_agent.message_format import format_validation_failure_message

            reply = format_validation_failure_message(
                validation.get("failures") or [],
                validation_result=validation,
                executor_result=executor_result,
                session=session,
            )

    # Append reply as assistant message if it exists and hasn't been added yet
    if reply:
        from ado2gh.agents.migration_agent.message_format import orchestrator_chat_already_published

        if not orchestrator_chat_already_published(session, reply):
            publish_orchestrator_chat(session, reply)

    # Sync pending_form from graph state to session so the client can see it
    pending_form = state.get("pending_form") or session.get("pending_form")
    if pending_form:
        session["pending_form"] = pending_form

    # T062: Graph turn complete — always return to idle so chat/forms work.
    validation = state.get("validation_result") or {}
    if validation.get("passed"):
        release_session_for_chat(session)
    elif _is_pev_max_retries_exhausted(state, session) or state.get("error"):
        release_session_for_chat(session, outcome="failed")
    else:
        set_session_idle(session)

    # T098: Release all repo locks on session end
    session_id = session.get("session_id", "")
    if session_id:
        try:
            from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
            store = MigrationSessionStore()
            store.release_all_locks(session_id)
        except Exception:
            # Non-fatal: proceed if store unavailable
            pass

    # Drain any remaining queued messages
    if _has_queued_messages(session):
        queued = _drain_message_queue(session)
        for msg in queued:
            _append_event(session, role="user", content=msg, kind="message")

    result: dict[str, Any] = {"should_return": True}
    if pending_form:
        result["pending_form"] = pending_form
    if reply:
        result["reply"] = reply
    return result
