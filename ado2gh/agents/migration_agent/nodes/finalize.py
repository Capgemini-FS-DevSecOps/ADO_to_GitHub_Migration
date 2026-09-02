"""finalize.py module."""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.message_format import compose_orchestrator_chat_message
from ado2gh.agents.migration_agent.nodes._common import _is_pev_max_retries_exhausted
from ado2gh.agents.migration_agent.session.state import release_session_for_chat, set_session_idle
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    _drain_message_queue,
    _has_queued_messages,
    publish_orchestrator_chat,
)

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
            from ado2gh.agents.migration_agent.session.store import MigrationSessionStore
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
