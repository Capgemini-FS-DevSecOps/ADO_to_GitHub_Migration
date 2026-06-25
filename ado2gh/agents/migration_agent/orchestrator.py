"""Orchestrator entry points for the migration agent.

Provides process_user_message() and stream_user_message() that compile
the LangGraph once and invoke it with thread_id=session_id for checkpointing.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator
from langchain_core.messages import HumanMessage

from ado2gh.agents.migration_agent.constants import (
    GRAPH_RECURSION_LIMIT,
    SSE_HEARTBEAT_INTERVAL_SECONDS,
)
from ado2gh.agents.migration_agent.graph import get_compiled_graph
from ado2gh.agents.migration_agent.llm_bridge import resolve_langchain_llm
from ado2gh.agents.migration_agent.session_state import OrchestratorResult, release_session_for_chat, set_session_idle


def _build_initial_state(
    session: dict[str, Any],
    user_message: str,
    *,
    model_id: str | None = None,
    accel_get: Any = None,
    accel_post: Any = None,
    build_plan: Any = None,
    session_token: str | None = None,
) -> dict[str, Any]:
    """Build the initial AgentState for a graph invocation."""
    llm, degraded, unconfigured, capabilities = resolve_langchain_llm(model_id)
    max_budget = capabilities.max_token_budget if capabilities else 0
    return {
        "messages": [HumanMessage(content=user_message)] if user_message else [],
        "user_message": user_message,
        "session": session,
        "llm": llm,
        "llm_degraded": degraded,
        "llm_unconfigured": unconfigured,
        "capabilities": capabilities,
        "accel_get": accel_get,
        "accel_post": accel_post,
        "build_plan": build_plan,
        "session_token": session_token,
        "intent": "",
        "thinking": None,
        "reply": None,
        "tool_calls": [],
        "parsed": {},
        "iteration": 0,
        "max_iterations": 20,
        "accumulated_tokens": 0,
        "max_token_budget": max_budget,
        "start_pev": False,
        "start_execution": bool(session.get("start_execution")),
        "pending_form": None,
        "form_submission": None,  # T101: Handle form submissions
        "should_return": False,
        "error": None,
        "pev_active": False,
        "pev_retry_count": 0,
        "inter_agent_messages": [],
        "migration_plan": session.get("migration_plan"),
        "executor_result": None,
        "validation_result": None,
        "validation_feedback": None,
        "pending_clarification": None,
        "cycle_summaries": [],
        "migration_queue": None,
        "rollback_records": [],
        "streaming_tokens": [],
        "message_queue": [],
    }


async def process_user_message(
    session: dict[str, Any],
    user_message: str,
    *,
    model_id: str | None = None,
    accel_get: Any = None,
    accel_post: Any = None,
    build_plan: Any = None,
    session_token: str | None = None,
) -> OrchestratorResult:
    """Process a user message through the LangGraph agent (non-streaming).

    Returns an OrchestratorResult with reply, start_pev, and pending_form.
    """
    graph = get_compiled_graph()
    from ado2gh.agents.migration_agent.metrics import record_pev_cycle
    record_pev_cycle()
    initial_state = _build_initial_state(
        session,
        user_message,
        model_id=model_id,
        accel_get=accel_get,
        accel_post=accel_post,
        build_plan=build_plan,
        session_token=session_token,
    )

    thread_id = str(session.get("session_id") or "default")
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}

    final_state: dict[str, Any] = {}
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
    finally:
        set_session_idle(session)

    pending_form = final_state.get("pending_form") or session.get("pending_form")
    if pending_form:
        session["pending_form"] = pending_form

    # T062: Persist session state after graph execution
    session_id = session.get("session_id", "")
    validation = final_state.get("validation_result") or {}
    if validation.get("passed") or session.get("pev_execution_completed"):
        release_session_for_chat(session)

    if session_id:
        try:
            from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
            from ado2gh.agents.migration_agent.session_lifecycle import persist_session_snapshot

            store = MigrationSessionStore()
            store.save_session_state(session_id, final_state)
            persist_session_snapshot(session)
        except Exception:
            pass  # Non-fatal — state is also in LangGraph checkpointer

    return OrchestratorResult(
        reply=final_state.get("reply", ""),
        tasks=session.get("tasks", []),
        pending_form=pending_form,
    )


async def stream_user_message(
    session: dict[str, Any],
    user_message: str,
    *,
    model_id: str | None = None,
    accel_get: Any = None,
    accel_post: Any = None,
    build_plan: Any = None,
    session_token: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream agent events via SSE as the graph executes.

    Uses LangGraph's native stream_mode=["custom", "updates"]:
    - "custom": tokens/thinking streamed live from nodes via get_stream_writer()
    - "updates": node completions (reply, tool_calls, pending_form, etc.)
    """
    graph = get_compiled_graph()
    from ado2gh.agents.migration_agent.metrics import record_pev_cycle
    record_pev_cycle()
    initial_state = _build_initial_state(
        session,
        user_message,
        model_id=model_id,
        accel_get=accel_get,
        accel_post=accel_post,
        build_plan=build_plan,
        session_token=session_token,
    )

    thread_id = str(session.get("session_id") or "default")
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}

    final_state: dict[str, Any] = {}

    # Track session messages to emit tool_result and status SSE events
    prev_msg_count = len(session.get("messages", []))
    prev_status = session.get("status", "")

    try:
        async for stream_mode, event in graph.astream(
            initial_state, config=config, stream_mode=["custom", "updates"],
        ):
            if stream_mode == "custom":
                # Live tokens/thinking from get_stream_writer() inside nodes
                if isinstance(event, dict):
                    yield event
                continue

            if stream_mode != "updates":
                continue

            # event is a dict of {node_name: state_update}
            for node_name, update in event.items() if isinstance(event, dict) else []:
                if not isinstance(update, dict):
                    continue

                # Stream reply — include messages already published to session chat
                reply = update.get("reply")
                if reply:
                    yield {"kind": "message", "content": reply, "subagent": _node_to_subagent(node_name)}

                # Stream tool calls
                tool_calls = update.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        yield {
                            "kind": "tool_call",
                            "content": tc.get("name", ""),
                            "subagent": _node_to_subagent(node_name),
                            "meta": tc,
                        }

                # Stream pending form
                pending_form = update.get("pending_form")
                if pending_form:
                    session["pending_form"] = pending_form
                    yield {
                        "kind": "form_request",
                        "content": pending_form.get("title", ""),
                        "subagent": _node_to_subagent(node_name),
                        "meta": pending_form,
                    }

                # Emit tool_result events from new session messages.
                # Thinking events for all agents are already streamed live via
                # get_stream_writer() (orchestrator uses it directly, planner/
                # executor/validator use _append_and_stream).
                current_msgs = session.get("messages", [])
                new_msgs = current_msgs[prev_msg_count:]
                for msg in new_msgs:
                    msg_kind = msg.get("kind", "")
                    if msg_kind not in (
                        "thinking", "status", "progress", "tool_call", "tool_result", "task_update",
                    ):
                        continue
                    meta = msg.get("meta")
                    if msg_kind == "tool_result":
                        meta = {
                            "tool_name": msg.get("tool_name", ""),
                            "guardrail_decision": msg.get("guardrail_decision"),
                            **(meta or {}),
                        }
                    yield {
                        "kind": msg_kind,
                        "content": msg.get("content", ""),
                        "subagent": msg.get("subagent", _node_to_subagent(node_name)),
                        "meta": meta,
                    }
                prev_msg_count = len(current_msgs)

                # Emit status event when session status changes
                current_status = session.get("status", "")
                if current_status and current_status != prev_status:
                    yield {
                        "kind": "status",
                        "content": current_status,
                        "subagent": _node_to_subagent(node_name),
                    }
                    prev_status = current_status

                # Merge into final state
                final_state.update(update)
    finally:
        set_session_idle(session)

    # T063: Persist session state after streaming graph execution
    session_id = session.get("session_id", "")
    validation = final_state.get("validation_result") or {}
    if validation.get("passed") or session.get("pev_execution_completed"):
        release_session_for_chat(session)

    if session_id:
        try:
            from ado2gh.agents.migration_agent.session_store import MigrationSessionStore
            from ado2gh.agents.migration_agent.session_lifecycle import persist_session_snapshot

            store = MigrationSessionStore()
            store.save_session_state(session_id, final_state)
            persist_session_snapshot(session)
        except Exception:
            pass  # Non-fatal — state is also in LangGraph checkpointer

    # Emit done event
    pending_form = final_state.get("pending_form") or session.get("pending_form")
    if pending_form:
        session["pending_form"] = pending_form

    yield {
        "kind": "done",
        "content": "",
        "subagent": "orchestrator",
        "__done__": True,
        "reply": final_state.get("reply", ""),
        "pending_form": pending_form,
    }


def _node_to_subagent(node_name: str) -> str:
    """Map graph node names to subagent labels for SSE events."""
    mapping = {
        "classify_intent": "orchestrator",
        "orchestrator": "orchestrator",
        "planner": "planner",
        "executor": "executor",
        "validator": "validator",
        "execute_tools": "orchestrator",
        "finalize": "orchestrator",
    }
    return mapping.get(node_name, "orchestrator")
