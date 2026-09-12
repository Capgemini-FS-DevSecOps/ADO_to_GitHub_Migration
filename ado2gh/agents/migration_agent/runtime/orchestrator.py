"""Orchestrator entry points for the migration agent.

Provides process_user_message() and stream_user_message() that compile
the LangGraph once and invoke it with thread_id=session_id for checkpointing.

Every public entry point takes the same optional ``deps`` mapping. Its keys are
the per-invocation runtime dependencies declared by ``_RUNTIME_KEYS`` in
``ado2gh/agents/migration_agent/runtime/deps.py`` — callers supply
``accel_get``, ``accel_post``, ``build_plan`` and ``session_token``; ``llm`` and
``capabilities`` are resolved here from the session's ``selected_model_id``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from ado2gh.agents.migration_agent.constants import (
    GRAPH_RECURSION_LIMIT,
)
from ado2gh.agents.migration_agent.graph import get_compiled_graph
from ado2gh.agents.migration_agent.runtime.deps import clear_runtime_deps, set_runtime_deps
from ado2gh.agents.migration_agent.runtime.llm_bridge import resolve_langchain_llm
from ado2gh.agents.migration_agent.runtime.tracing import graph_run_config
from ado2gh.agents.migration_agent.session.state import OrchestratorResult, release_session_for_chat, set_session_idle

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from ado2gh.agents.migration_agent.runtime.llm_bridge import ModelCapabilities

logger = logging.getLogger(__name__)

# Keys preserved across turns (checkpoint + session); do not reset to zero each invoke.
_PERSISTENT_STATE_KEYS = (
    "pev_retry_count",
    "iteration",
    "migration_plan",
    "cycle_summaries",
    "migration_queue",
    "validation_feedback",
    "pending_form",
)


def _bind_runtime_deps(
    session: dict[str, Any],
    deps: dict[str, Any] | None = None,
) -> tuple[bool, bool, ModelCapabilities | None]:
    """Store non-serializable deps for the current graph invocation.

    The LLM client is resolved from ``session["selected_model_id"]`` and merged
    into ``deps`` before binding, so callers never pass a model id separately.

    Args:
        session: Session dict; its ``selected_model_id`` selects the LLM.
        deps: Runtime dependencies keyed by the names in ``runtime/deps.py``
            (``accel_get``, ``accel_post``, ``build_plan``, ``session_token``).

    Returns:
        A ``(degraded, unconfigured, capabilities)`` triple: whether the resolved
        model is a stub/offline fallback, whether no usable model is configured,
        and the detected model capabilities (None when unresolved).
    """
    llm, degraded, unconfigured, capabilities = resolve_langchain_llm(session.get("selected_model_id"))
    set_runtime_deps(**{**(deps or {}), "llm": llm, "capabilities": capabilities})
    return degraded, unconfigured, capabilities


def _build_initial_state(
    session: dict[str, Any],
    user_message: str,
    *,
    deps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the initial AgentState for a graph invocation and bind runtime deps.

    Args:
        session: Session dict carrying prior counters and the selected model.
        user_message: Text of the turn; an empty string starts with no messages.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Returns:
        A fresh AgentState dict seeded from the session (iteration counters,
        migration plan, PEV retry count) with all per-turn fields reset.
    """
    degraded, unconfigured, capabilities = _bind_runtime_deps(session, deps)
    max_budget = capabilities.max_token_budget if capabilities else 0
    return {
        "messages": [HumanMessage(content=user_message)] if user_message else [],
        "user_message": user_message,
        "session": session,
        "llm_degraded": degraded,
        "llm_unconfigured": unconfigured,
        "intent": "",
        "thinking": None,
        "reply": None,
        "tool_calls": [],
        "parsed": {},
        "iteration": int(session.get("iteration_count") or 0),
        "max_iterations": 20,
        "accumulated_tokens": 0,
        "max_token_budget": max_budget,
        "start_pev": False,
        "start_execution": bool(session.get("start_execution")),
        "pending_form": None,
        "form_submission": None,
        "should_return": False,
        "error": None,
        "pev_active": False,
        "pev_retry_count": int(session.get("pev_retry_count") or 0),
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


async def _merge_checkpoint_state(
    graph: CompiledStateGraph,
    config: dict[str, Any],
    turn_state: dict[str, Any],
) -> dict[str, Any]:
    """Merge prior thread checkpoint into turn input (LangGraph persistence best practice).

    Returns:
        ``turn_state``, with the keys in ``_PERSISTENT_STATE_KEYS`` overwritten
        from the thread's last checkpoint. Returned unchanged when the graph has
        no checkpointer, has no snapshot yet, or the snapshot read fails.
    """
    if getattr(graph, "checkpointer", None) is None:
        return turn_state
    try:
        snapshot = await graph.aget_state(config)
        if not snapshot or not snapshot.values:
            return turn_state
        prior = snapshot.values
        for key in _PERSISTENT_STATE_KEYS:
            if prior.get(key) is not None:
                turn_state[key] = prior[key]
    except Exception as exc:
        logger.debug("Checkpoint merge skipped: %s", exc)
    return turn_state


def _state_has_interrupt(final_state: dict[str, Any]) -> bool:
    """Detect a LangGraph interrupt (HITL form) recorded in a state dict.

    Returns:
        True when the state carries ``__interrupt__`` or a non-empty
        ``interrupts`` entry, meaning the graph paused for operator input.
    """
    if final_state.get("__interrupt__"):
        return True
    interrupts = final_state.get("interrupts")
    return bool(interrupts)


async def is_graph_interrupted(session_id: str) -> bool:
    """Check the persisted checkpoint for a thread paused at an interrupt.

    Returns:
        True when the thread's snapshot or any of its pending tasks holds an
        interrupt. False when there is no checkpointer, no snapshot, or the
        snapshot read fails.
    """
    graph = await get_compiled_graph()
    if getattr(graph, "checkpointer", None) is None:
        return False
    config = graph_run_config(str(session_id), recursion_limit=GRAPH_RECURSION_LIMIT)
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.debug("Interrupt check skipped: %s", exc)
        return False
    if not snapshot:
        return False
    interrupts = getattr(snapshot, "interrupts", None)
    if interrupts:
        return True
    for task in getattr(snapshot, "tasks", ()) or ():
        if getattr(task, "interrupts", None):
            return True
    return False


async def resume_interrupted_graph(
    session: dict[str, Any],
    resume_value: dict[str, Any],
    *,
    deps: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Resume a paused graph with Command(resume=...) per LangGraph HITL docs.

    Args:
        session: Session dict for the paused thread.
        resume_value: Payload handed to the waiting ``interrupt()`` call, e.g.
            ``{"form_id": ..., "values": ...}`` for a HITL form submission.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Returns:
        An OrchestratorResult with the final reply, the session's task list, and
        the pending form when the graph paused again on another interrupt.
    """
    graph = await get_compiled_graph()
    thread_id = str(session.get("session_id") or "default")
    config = graph_run_config(thread_id, recursion_limit=GRAPH_RECURSION_LIMIT)
    _bind_runtime_deps(session, deps)

    final_state: dict[str, Any] = {}
    interrupted = False
    try:
        final_state = await graph.ainvoke(Command(resume=resume_value), config=config)
        interrupted = _state_has_interrupt(final_state) or await is_graph_interrupted(thread_id)
    finally:
        clear_runtime_deps()
        if not interrupted:
            set_session_idle(session)

    pending_form = final_state.get("pending_form") or session.get("pending_form")
    if pending_form:
        session["pending_form"] = pending_form

    _persist_session_counters(session, final_state)

    session_id = session.get("session_id", "")
    validation = final_state.get("validation_result") or {}
    if validation.get("passed") or session.get("pev_execution_completed"):
        release_session_for_chat(session)

    if session_id:
        try:
            from ado2gh.agents.migration_agent.session.lifecycle import persist_session_snapshot
            from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

            store = MigrationSessionStore()
            store.save_session_state(session_id, final_state)
            persist_session_snapshot(session)
        except Exception as exc:
            logger.warning("Session persist failed for %s: %s", session_id, exc)

    return OrchestratorResult(
        reply=final_state.get("reply", ""),
        tasks=session.get("tasks", []),
        pending_form=pending_form,
    )


async def continue_session_graph(
    session: dict[str, Any],
    user_message: str,
    *,
    resume_value: dict[str, Any] | None = None,
    deps: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Resume an interrupted thread or start/continue a normal user turn.

    Args:
        session: Session dict for the thread.
        user_message: Text used when the thread is not paused at an interrupt.
        resume_value: Interrupt payload; when set and the thread really is
            paused, the graph resumes instead of starting a new turn.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Returns:
        The OrchestratorResult of whichever path ran — ``resume_interrupted_graph``
        or ``process_user_message``.
    """
    session_id = str(session.get("session_id") or "default")
    if resume_value is not None and await is_graph_interrupted(session_id):
        return await resume_interrupted_graph(session, resume_value, deps=deps)
    return await process_user_message(session, user_message, deps=deps)


def _persist_session_counters(session: dict[str, Any], final_state: dict[str, Any]) -> None:
    """Sync PEV counters back to session dict for UI and MigrationSessionStore."""
    if "pev_retry_count" in final_state:
        session["pev_retry_count"] = final_state["pev_retry_count"]
    if "iteration" in final_state:
        session["iteration_count"] = final_state["iteration"]
    if final_state.get("migration_plan"):
        session["migration_plan"] = final_state["migration_plan"]


async def process_user_message(
    session: dict[str, Any],
    user_message: str,
    *,
    deps: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Process a user message through the LangGraph agent (non-streaming).

    Args:
        session: Session dict; mutated in place with counters, pending form and
            status, then persisted via MigrationSessionStore.
        user_message: The operator's message for this turn.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Returns:
        An OrchestratorResult with the final reply, the session's task list, and
        the pending form when the graph paused on a HITL interrupt.
    """
    graph = await get_compiled_graph()
    from ado2gh.agents.metrics import get_metrics_collector
    get_metrics_collector().record_pev_cycle()
    initial_state = _build_initial_state(session, user_message, deps=deps)

    thread_id = str(session.get("session_id") or "default")
    config = graph_run_config(thread_id, recursion_limit=GRAPH_RECURSION_LIMIT)
    initial_state = await _merge_checkpoint_state(graph, config, initial_state)

    final_state: dict[str, Any] = {}
    interrupted = False
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        interrupted = _state_has_interrupt(final_state) or await is_graph_interrupted(thread_id)
    finally:
        clear_runtime_deps()
        if not interrupted:
            set_session_idle(session)

    pending_form = final_state.get("pending_form") or session.get("pending_form")
    if pending_form:
        session["pending_form"] = pending_form

    _persist_session_counters(session, final_state)

    # T062: Persist session state after graph execution
    session_id = session.get("session_id", "")
    validation = final_state.get("validation_result") or {}
    if validation.get("passed") or session.get("pev_execution_completed"):
        release_session_for_chat(session)

    if session_id:
        try:
            from ado2gh.agents.migration_agent.session.lifecycle import persist_session_snapshot
            from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

            store = MigrationSessionStore()
            store.save_session_state(session_id, final_state)
            persist_session_snapshot(session)
        except Exception as exc:
            logger.warning("Session persist failed for %s: %s", session_id, exc)

    return OrchestratorResult(
        reply=final_state.get("reply", ""),
        tasks=session.get("tasks", []),
        pending_form=pending_form,
    )


async def _stream_graph_events(
    session: dict[str, Any],
    graph: CompiledStateGraph,
    config: dict[str, Any],
    graph_input: dict[str, Any] | Command,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream LangGraph custom + updates events; yields done payload last.

    Args:
        session: Session dict; mutated in place and persisted when the run ends.
        graph: The compiled agent graph.
        config: LangGraph invoke config from ``graph_run_config``.
        graph_input: Initial AgentState for a new turn, or ``Command(resume=...)``
            to continue a thread paused at an interrupt.

    Yields:
        SSE-shaped event dicts: the ``token`` and ``thinking`` events written by
        nodes via ``get_stream_writer()`` are relayed verbatim, plus ``message``,
        ``tool_call``, ``status`` and ``form_request`` events derived from state
        updates, then one final ``done`` event carrying ``reply`` and
        ``pending_form``.
    """
    final_state: dict[str, Any] = {}
    prev_msg_count = len(session.get("messages", []))
    prev_status = session.get("status", "")
    thread_id = str(session.get("session_id") or "default")

    try:
        async for stream_mode, event in graph.astream(
            graph_input,
            config=config,
            stream_mode=["custom", "updates"],
        ):
            if stream_mode == "custom":
                if isinstance(event, dict):
                    yield event
                continue

            if stream_mode != "updates":
                continue

            for node_name, update in event.items() if isinstance(event, dict) else []:
                if not isinstance(update, dict):
                    continue

                reply = update.get("reply")
                if reply:
                    yield {"kind": "message", "content": reply, "subagent": _node_to_subagent(node_name)}

                tool_calls = update.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        yield {
                            "kind": "tool_call",
                            "content": tc.get("name", ""),
                            "subagent": _node_to_subagent(node_name),
                            "meta": tc,
                        }

                pending_form = update.get("pending_form")
                if pending_form:
                    session["pending_form"] = pending_form
                    yield {
                        "kind": "form_request",
                        "content": pending_form.get("title", ""),
                        "subagent": _node_to_subagent(node_name),
                        "meta": pending_form,
                    }

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

                current_status = session.get("status", "")
                if current_status and current_status != prev_status:
                    yield {
                        "kind": "status",
                        "content": current_status,
                        "subagent": _node_to_subagent(node_name),
                    }
                    prev_status = current_status

                final_state.update(update)

                if _state_has_interrupt(update):
                    for intr in update.get("__interrupt__") or ():
                        payload = getattr(intr, "value", intr)
                        if isinstance(payload, dict) and payload.get("kind") == "form_request":
                            form = payload.get("form") or {}
                            session["pending_form"] = form
                            yield {
                                "kind": "form_request",
                                "content": payload.get("title", ""),
                                "subagent": "orchestrator",
                                "meta": form,
                            }
    finally:
        clear_runtime_deps()
        interrupted = _state_has_interrupt(final_state) or await is_graph_interrupted(thread_id)
        if not interrupted:
            set_session_idle(session)

    _persist_session_counters(session, final_state)

    session_id = session.get("session_id", "")
    validation = final_state.get("validation_result") or {}
    if validation.get("passed") or session.get("pev_execution_completed"):
        release_session_for_chat(session)

    if session_id:
        try:
            from ado2gh.agents.migration_agent.session.lifecycle import persist_session_snapshot
            from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

            store = MigrationSessionStore()
            store.save_session_state(session_id, final_state)
            persist_session_snapshot(session)
        except Exception as exc:
            logger.warning("Session persist failed for %s: %s", session_id, exc)

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


async def stream_user_message(
    session: dict[str, Any],
    user_message: str,
    *,
    deps: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream agent events via SSE as the LangGraph PEV workflow executes.

    Uses LangGraph ``stream_mode=["custom", "updates"]``: custom events from
    ``get_stream_writer()`` in nodes; updates for reply, forms, and tool calls.

    Args:
        session: Session dict; mutated in place and persisted when the run ends.
        user_message: The operator's message for this turn.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Yields:
        The SSE event dicts produced by ``_stream_graph_events``.
    """
    graph = await get_compiled_graph()
    from ado2gh.agents.metrics import get_metrics_collector
    get_metrics_collector().record_pev_cycle()
    initial_state = _build_initial_state(session, user_message, deps=deps)

    thread_id = str(session.get("session_id") or "default")
    config = graph_run_config(thread_id, recursion_limit=GRAPH_RECURSION_LIMIT)
    initial_state = await _merge_checkpoint_state(graph, config, initial_state)

    async for event in _stream_graph_events(session, graph, config, initial_state):
        yield event


async def stream_interrupted_graph(
    session: dict[str, Any],
    resume_value: dict[str, Any],
    *,
    deps: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Resume a paused graph via Command(resume=...) and stream SSE events.

    Args:
        session: Session dict for the paused thread.
        resume_value: Payload handed to the waiting ``interrupt()`` call.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Yields:
        The SSE event dicts produced by ``_stream_graph_events``.
    """
    graph = await get_compiled_graph()
    from ado2gh.agents.metrics import get_metrics_collector

    get_metrics_collector().record_pev_cycle()
    thread_id = str(session.get("session_id") or "default")
    config = graph_run_config(thread_id, recursion_limit=GRAPH_RECURSION_LIMIT)
    _bind_runtime_deps(session, deps)

    async for event in _stream_graph_events(session, graph, config, Command(resume=resume_value)):
        yield event


async def continue_session_graph_stream(
    session: dict[str, Any],
    user_message: str,
    *,
    resume_value: dict[str, Any] | None = None,
    deps: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream resume for interrupted thread or a normal user turn.

    Args:
        session: Session dict for the thread.
        user_message: Text used when the thread is not paused at an interrupt.
        resume_value: Interrupt payload; when set and the thread really is
            paused, the graph resumes instead of starting a new turn.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Yields:
        The SSE event dicts of whichever path ran — ``stream_interrupted_graph``
        or ``stream_user_message``.
    """
    session_id = str(session.get("session_id") or "default")
    if resume_value is not None and await is_graph_interrupted(session_id):
        async for event in stream_interrupted_graph(session, resume_value, deps=deps):
            yield event
        return

    async for event in stream_user_message(session, user_message, deps=deps):
        yield event


def _node_to_subagent(node_name: str) -> str:
    """Map graph node names to subagent labels for SSE events.

    Returns:
        The subagent label the UI renders for that node — ``planner``,
        ``executor``, ``validator``, or ``orchestrator`` for anything else.
    """
    mapping = {
        "classify_intent": "orchestrator",
        "orchestrator": "orchestrator",
        "planner": "planner",
        "executor": "executor",
        "validator": "validator",
        "execute_tools": "orchestrator",
        "human_input": "orchestrator",
        "finalize": "orchestrator",
    }
    return mapping.get(node_name, "orchestrator")


async def run_turn(
    session: dict[str, Any],
    user_message: str,
    *,
    deps: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Run one user turn through the LangGraph PEV workflow.

    Args:
        session: Session dict for the thread.
        user_message: The operator's message for this turn.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Returns:
        The OrchestratorResult from ``process_user_message``.
    """
    return await process_user_message(session, user_message, deps=deps)


async def stream_turn(
    session: dict[str, Any],
    user_message: str,
    *,
    deps: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream SSE-shaped events for one user turn.

    Args:
        session: Session dict for the thread.
        user_message: The operator's message for this turn.
        deps: Runtime dependencies — see ``runtime/deps.py`` ``_RUNTIME_KEYS``.

    Yields:
        The SSE event dicts produced by ``stream_user_message``.
    """
    async for event in stream_user_message(session, user_message, deps=deps):
        yield event
