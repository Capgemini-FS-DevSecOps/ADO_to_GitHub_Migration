"""Shared helpers for all PEV graph nodes."""
from __future__ import annotations

import json
from typing import Any

from ado2gh.agents.migration_agent.constants import (
    MAX_PEV_RETRIES,
)
from ado2gh.agents.migration_agent.session.state import (
    SessionState,
    release_session_for_chat,
)
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    publish_orchestrator_chat,
)


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
    from ado2gh.agents.migration_agent.hitl.operator_input import build_repo_lock_failure

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
    from ado2gh.agents.migration_agent.hitl.operator_input import (
        assess_operator_input_needed,
        pending_operator_input,
    )
    from ado2gh.agents.migration_agent.hitl.schemas import OperatorInputRequest

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
    from ado2gh.agents.migration_agent.hitl.operator_input import (
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

    from ado2gh.agents.migration_agent.nodes.validator import _failure_is_benign

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
            from ado2gh.agents.migration_agent.hitl.operator_input import (
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
    from ado2gh.agents.migration_agent.hitl.operator_input import (
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
    from ado2gh.agents.migration_agent.hitl.forms import _plan_confirmation_reply, sanitize_form
    from ado2gh.agents.migration_agent.hitl.intake import build_plan_review_form

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
    from ado2gh.agents.migration_agent.session.state import set_session_phase

    set_session_phase(session, target)


