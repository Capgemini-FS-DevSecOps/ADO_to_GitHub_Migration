"""orchestrator.py module."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ado2gh.agents.migration_agent.constants import NO_LLM_CONFIGURED_MESSAGE
from ado2gh.agents.migration_agent.nodes._common import (
    _is_pev_max_retries_exhausted,
    _is_start_execution_message,
    _present_operator_input,
    _present_pev_escalation_to_operator,
    _present_plan_confirmation,
    _present_validation_failure_to_operator,
    _transition_session,
)
from ado2gh.agents.migration_agent.nodes.intent import (
    _begin_new_agent_migration,
    _build_session_context,
    _classify_user_intent,
)
from ado2gh.agents.migration_agent.nodes.orchestrator_tools import (
    _apply_orchestrator_tools,
    _execute_rollback,
)
from ado2gh.agents.migration_agent.nodes.planner import _build_migration_queue_from_plan
from ado2gh.agents.migration_agent.nodes.streaming import _stream_llm_response
from ado2gh.agents.migration_agent.prompts import get_prompt
from ado2gh.agents.migration_agent.runtime.context_window import build_context_with_cycle_summaries
from ado2gh.agents.migration_agent.session.state import SessionState, release_session_for_chat
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    _is_cancellation_request,
    _parse_llm_json,
    _queue_user_message,
    _reset_turn_status_budget,
    _safe_reply,
    publish_orchestrator_chat,
)

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
            from ado2gh.agents.migration_agent.hitl.operator_input import (
                assess_operator_input_needed,
                pending_operator_input,
            )
            from ado2gh.agents.migration_agent.hitl.schemas import OperatorInputRequest

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
            from ado2gh.agents.migration_agent.hitl.schemas import OperatorInputRequest

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

            from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
            from ado2gh.agents.migration_agent.hitl.intake import build_repo_error_form

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

        if form_id in ("cancellation_options", "intake_cancellation_action"):
            action = values.get("action") or values.get("cancellation_action", "")
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
            from ado2gh.agents.migration_agent.hitl.form_fields import build_field_recommendations
            from ado2gh.agents.migration_agent.hitl.forms import sanitize_form
            from ado2gh.agents.migration_agent.hitl.intake import build_dynamic_form
            from ado2gh.agents.migration_agent.hitl.schemas import INTAKE_FIELD_REGISTRY

            rollback_records = state.get("rollback_records", [])
            has_rollback = bool(rollback_records)
            planner_context = {
                "session": session,
                "field_recommendations": build_field_recommendations(
                    session,
                    missing_fields=["cancellation_action"],
                ),
            }
            if not has_rollback:
                opts = planner_context["field_recommendations"].get("cancellation_action", {}).get("options", [])
                planner_context["field_recommendations"]["cancellation_action"]["options"] = [
                    o for o in opts if o.get("value") != "rollback"
                ]
            form = build_dynamic_form(
                form_id="intake_cancellation_action",
                title="Migration cancellation",
                description=(
                    "Choose how to handle the cancelled migration."
                    + (" Rollback can remove session-created GitHub resources." if has_rollback else "")
                ),
                fields=[INTAKE_FIELD_REGISTRY["cancellation_action"]],
                planner_context=planner_context,
            )
            if has_rollback:
                form = sanitize_form({
                    **form,
                    "fields": [
                        *form["fields"],
                        {
                            "name": "confirm_rollback",
                            "label": "Confirm rollback",
                            "type": "checkbox",
                            "description": "Delete GitHub resources created by this session.",
                            "required": True,
                        },
                    ],
                })
            session["status"] = "idle"
            _append_event(session, role="system", content="Migration cancelled by operator. Select rollback option.", kind="message")
            return {"should_return": True, "reply": "Migration cancelled. Please choose an action below.", "pending_form": form}
        _queue_user_message(session, user_message)
        return {"should_return": True, "reply": "Message queued — I'll process it after the current migration step completes."}

    # Active intake workflow — route by schema even when intent is general_chat
    # (e.g. operator replies "dry run" after selecting a repository).
    # Skip when planner already produced a plan awaiting operator review.
    from ado2gh.agents.migration_agent.hitl.intake import IntakePhase, determine_intake_phase

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
            from langgraph.config import get_stream_writer

            w = get_stream_writer()
            if w:
                w({"kind": "thinking", "content": thinking, "subagent": "orchestrator"})

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
            publish_orchestrator_chat(session, reply)
        return {
            "tool_calls": tool_calls,
            "thinking": thinking,
            "should_return": False,
            "reply": reply if not has_form_tool and not has_invoke_planner else None,
        }

    reply = publish_orchestrator_chat(session, _safe_reply(parsed, response_text))
    return {"should_return": True, "reply": reply, "thinking": thinking}


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
            from langgraph.config import get_stream_writer

            w = get_stream_writer()
            if w:
                w({"kind": "thinking", "content": thinking, "subagent": "orchestrator"})
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
    from ado2gh.agents.migration_agent.hitl.intake import analysis_from_state, resolve_intake_routing

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
        from ado2gh.agents.migration_agent.hitl.intake_guardrails import repository_confirmed_for_turn

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
        publish_orchestrator_chat(session, synth_reply)
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
            from langgraph.config import get_stream_writer

            w = get_stream_writer()
            if w:
                w({"kind": "thinking", "content": thinking, "subagent": "orchestrator"})

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
                publish_orchestrator_chat(session, reply)
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

