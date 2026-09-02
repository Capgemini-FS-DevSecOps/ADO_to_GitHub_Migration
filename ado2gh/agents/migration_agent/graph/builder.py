"""LangGraph StateGraph for the migration agent (Google ADK + LangGraph layout).

Direct PEV flow with strict agent boundaries:

    orchestrator <-> planner -> executor -> validator
                              ^              |
                              +--- retry -----+

Graph topology lives here; node implementations in ``nodes/``. Runtime entry: ``runtime``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from ado2gh.agents.migration_agent.constants import (
    MAX_ITERATIONS,
    MAX_PEV_RETRIES,
)
from ado2gh.agents.migration_agent.graph.state import AgentState

logger = logging.getLogger(__name__)

# Core graph nodes
NODE_ORCHESTRATOR = "orchestrator"
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_VALIDATOR = "validator"
NODE_HUMAN_INPUT = "human_input"
NODE_FINALIZE = "finalize"

ALL_NODES = (
    NODE_ORCHESTRATOR,
    NODE_PLANNER,
    NODE_EXECUTOR,
    NODE_VALIDATOR,
    NODE_HUMAN_INPUT,
    NODE_FINALIZE,
)

# Legacy aliases (tests / streaming may reference these)
NODE_CLASSIFY_INTENT = NODE_ORCHESTRATOR
NODE_EXECUTE_TOOLS = NODE_ORCHESTRATOR

_CHECKPOINTER: Any = None
_CHECKPOINTER_LOOP: Any = None


async def _close_checkpointer(checkpointer: Any) -> None:
    """Close a checkpointer's DB connection so its worker thread exits.

    aiosqlite connections run a non-daemon worker thread; abandoning one on
    event-loop change leaks the thread and blocks interpreter shutdown.
    Cross-loop closes can wedge, so the wait is capped — on timeout the
    thread leaks (previous behavior) instead of hanging the caller.
    """
    conn = getattr(checkpointer, "conn", None)
    if conn is None:
        return
    try:
        await asyncio.wait_for(conn.close(), timeout=2)
    except Exception:
        logger.debug("stale checkpointer connection close failed", exc_info=True)


async def _get_checkpointer():
    """Build a LangGraph checkpointer based on storage backend.

    Production: AsyncPostgresSaver or AsyncSqliteSaver (durable, multi-worker safe).
    Development: InMemorySaver when checkpoint packages are missing.

    The graph runs exclusively through async entry points (``ainvoke``/``astream``/
    ``aget_state``) — the checkpointer must be the async variant, or every call
    raises ``NotImplementedError``. ``from_conn_string`` is a short-lived context
    manager; long-running services instead hold a persistent
    ``aiosqlite.Connection`` / ``psycopg.AsyncConnection``.

    The cached connection is bound to the event loop that created it — reused
    from a different loop, its internal lock raises ``RuntimeError``. Rebuild
    when the running loop changed (e.g. each pytest test / TestClient gets its
    own loop; a stable loop in production means this never rebuilds there).
    """
    global _CHECKPOINTER, _CHECKPOINTER_LOOP
    current_loop = asyncio.get_running_loop()
    if _CHECKPOINTER is not None:
        if _CHECKPOINTER_LOOP is current_loop:
            return _CHECKPOINTER
        stale, _CHECKPOINTER = _CHECKPOINTER, None
        await _close_checkpointer(stale)
    _CHECKPOINTER_LOOP = current_loop

    backend = os.environ.get("ADO2GH_STORAGE_BACKEND", "sqlite").lower()
    if backend in ("postgresql", "postgres"):
        try:
            import psycopg
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            db_uri = os.environ.get("ADO2GH_DATABASE_URL")
            if not db_uri:
                conn_info = {
                    "host": os.environ.get("PGHOST", "localhost"),
                    "port": int(os.environ.get("PGPORT", 5432)),
                    "user": os.environ.get("PGUSER", "ado2gh"),
                    "password": os.environ.get("PGPASSWORD", ""),
                    "dbname": os.environ.get("PGDATABASE", "ado2gh"),
                }
                db_uri = "postgresql://{user}:{password}@{host}:{port}/{dbname}".format(**conn_info)
            conn = await psycopg.AsyncConnection.connect(db_uri, autocommit=True)
            _CHECKPOINTER = AsyncPostgresSaver(conn)
            if hasattr(_CHECKPOINTER, "setup"):
                await _CHECKPOINTER.setup()
            logger.info("LangGraph checkpointer: AsyncPostgresSaver")
            return _CHECKPOINTER
        except ImportError:
            logger.warning(
                "langgraph-checkpoint-postgres not installed; falling back to SQLite checkpointer"
            )

    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        db_path = os.environ.get("ADO2GH_SQLITE_PATH", "data/agent_checkpoints.db")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = await aiosqlite.connect(db_path)
        _CHECKPOINTER = AsyncSqliteSaver(conn)
        if hasattr(_CHECKPOINTER, "setup"):
            await _CHECKPOINTER.setup()
        logger.info("LangGraph checkpointer: AsyncSqliteSaver (%s)", db_path)
        return _CHECKPOINTER
    except ImportError:
        pass

    try:
        from langgraph.checkpoint.memory import InMemorySaver

        logger.warning(
            "No durable checkpointer available; using InMemorySaver (dev/test only — "
            "install langgraph-checkpoint-sqlite for persistence)"
        )
        _CHECKPOINTER = InMemorySaver()
        return _CHECKPOINTER
    except ImportError:
        logger.error("No LangGraph checkpointer available; graph will not persist thread state")
        return None


# Per LangGraph fault-tolerance docs: retry transient LLM/API errors on async nodes.
_LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0)


def _with_runtime_deps(node_fn: Any) -> Any:
    """Inject per-invocation callables into node state (not checkpointed)."""
    from ado2gh.agents.migration_agent.runtime.deps import (
        merge_runtime_into_state,
        strip_runtime_deps,
    )

    async def wrapped(state: AgentState, config: RunnableConfig | None = None) -> Any:
        return strip_runtime_deps(await node_fn(merge_runtime_into_state(state)))

    return wrapped


async def _build_graph() -> Any:
    """Build and compile the migration agent StateGraph."""
    from ado2gh.agents.migration_agent.hitl.interrupt_node import human_input_node
    from ado2gh.agents.migration_agent.nodes import (
        executor_node,
        finalize_node,
        orchestrator_node,
        planner_node,
        validator_node,
    )

    graph = StateGraph(AgentState)

    graph.add_node(NODE_ORCHESTRATOR, _with_runtime_deps(orchestrator_node), retry_policy=_LLM_RETRY)
    graph.add_node(NODE_PLANNER, _with_runtime_deps(planner_node), retry_policy=_LLM_RETRY)
    graph.add_node(NODE_EXECUTOR, _with_runtime_deps(executor_node), retry_policy=_LLM_RETRY)
    graph.add_node(NODE_VALIDATOR, _with_runtime_deps(validator_node), retry_policy=_LLM_RETRY)
    graph.add_node(NODE_HUMAN_INPUT, _with_runtime_deps(human_input_node))
    graph.add_node(NODE_FINALIZE, _with_runtime_deps(finalize_node))

    graph.set_entry_point(NODE_ORCHESTRATOR)

    graph.add_conditional_edges(
        NODE_ORCHESTRATOR,
        _route_after_orchestrator,
        {
            "planner": NODE_PLANNER,
            "executor": NODE_EXECUTOR,
            "human_input": NODE_HUMAN_INPUT,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_conditional_edges(
        NODE_PLANNER,
        _route_after_planner,
        {
            "orchestrator": NODE_ORCHESTRATOR,
            "executor": NODE_EXECUTOR,
            "human_input": NODE_HUMAN_INPUT,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_edge(NODE_HUMAN_INPUT, NODE_ORCHESTRATOR)

    graph.add_conditional_edges(
        NODE_EXECUTOR,
        _route_after_executor,
        {
            "validator": NODE_VALIDATOR,
            "orchestrator": NODE_ORCHESTRATOR,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_conditional_edges(
        NODE_VALIDATOR,
        _route_after_validator,
        {
            "planner": NODE_PLANNER,
            "orchestrator": NODE_ORCHESTRATOR,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_edge(NODE_FINALIZE, END)

    checkpointer = await _get_checkpointer()
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


# ─── Routing (strict PEV boundaries) ──────────────────────────────────

def _needs_human_input(state: AgentState) -> bool:
    """True when a dynamic form must be collected via LangGraph interrupt()."""
    if state.get("form_submission"):
        return False
    pending = state.get("pending_form")
    if not pending:
        session = state.get("session") or {}
        pending = session.get("pending_form")
    return bool(pending)


def _route_after_orchestrator(state: AgentState) -> str:
    """Orchestrator hands off to planner, executor (approved plan), human input, or finalize."""
    if state.get("should_return"):
        if state.get("start_execution") or state.get("start_pev"):
            return "finalize"
        if _needs_human_input(state):
            return "human_input"
        return "finalize"
    if state.get("start_execution") and state.get("migration_plan"):
        return "executor"
    if state.get("start_execution") or state.get("start_pev"):
        return "planner"
    return "finalize"


def _route_after_planner(state: AgentState) -> str:
    """Planner sends work to executor, orchestrator, human input, or finalize."""
    if state.get("should_return"):
        if _needs_human_input(state):
            return "human_input"
        return "finalize"
    if state.get("pending_clarification"):
        return "orchestrator"

    planner_next = state.get("planner_next")
    if planner_next == "executor":
        return "executor"
    if planner_next == "orchestrator":
        return "orchestrator"

    session = state.get("session") or {}
    if state.get("migration_plan") and (
        state.get("start_execution") or session.get("plan_approved")
    ):
        return "executor"
    if state.get("migration_plan"):
        return "orchestrator"
    return "finalize"


def _route_after_executor(state: AgentState) -> str:
    """Executor hands results to validator, clarification back to orchestrator, or finalize."""
    if state.get("should_return"):
        return "finalize"
    if state.get("pending_clarification"):
        return "orchestrator"
    if state.get("executor_result"):
        return "validator"
    return "finalize"


def _route_after_validator(state: AgentState) -> str:
    """Route to orchestrator on pass/escalate/exhaustion, planner to retry."""
    if state.get("should_return"):
        return "finalize"
    validation_result = state.get("validation_result") or {}
    if validation_result.get("passed"):
        return "orchestrator"
    iteration = int(state.get("iteration") or 0)
    max_iterations = int(state.get("max_iterations") or MAX_ITERATIONS)
    retry_count = int(state.get("pev_retry_count") or 0)
    feedback = state.get("validation_feedback") or {}
    if feedback.get("escalate") or retry_count >= MAX_PEV_RETRIES or iteration >= max_iterations:
        return "orchestrator"
    return "planner"


# Legacy alias for tests that still import the old name
_route_after_execute_tools = _route_after_orchestrator


# ─── Compiled graph singleton ─────────────────────────────────────────

_compiled_graph: Any = None
_compiled_graph_loop: Any = None


async def get_compiled_graph() -> Any:
    """Return the compiled graph, building it on first call.

    Rebuilds when the running event loop changed — the compiled graph holds a
    direct reference to the checkpointer's connection, which is loop-bound
    (see ``_get_checkpointer``).
    """
    global _compiled_graph, _compiled_graph_loop
    current_loop = asyncio.get_running_loop()
    if _compiled_graph is None or _compiled_graph_loop is not current_loop:
        _compiled_graph = await _build_graph()
        _compiled_graph_loop = current_loop
    return _compiled_graph


def reset_compiled_graph() -> None:
    """Reset the compiled graph and close its checkpointer (useful for tests)."""
    global _compiled_graph, _compiled_graph_loop, _CHECKPOINTER, _CHECKPOINTER_LOOP
    _compiled_graph = None
    _compiled_graph_loop = None
    stale, _CHECKPOINTER = _CHECKPOINTER, None
    _CHECKPOINTER_LOOP = None
    if stale is not None and getattr(stale, "conn", None) is not None:
        try:
            asyncio.run(_close_checkpointer(stale))
        except Exception:
            logger.debug("checkpointer close on reset failed", exc_info=True)


async def clear_langgraph_thread(session_id: str) -> None:
    """Delete LangGraph checkpoint state for a session thread."""
    thread_id = str(session_id or "").strip()
    if not thread_id:
        return
    try:
        graph = await get_compiled_graph()
        checkpointer = getattr(graph, "checkpointer", None)
        if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
            await checkpointer.adelete_thread(thread_id)
    except Exception:
        pass


def clear_langgraph_thread_sync(session_id: str) -> None:
    """Best-effort ``clear_langgraph_thread`` for sync call sites.

    Schedules on the running loop when one is active (the normal FastAPI
    request path, non-blocking); runs to completion otherwise (scripts/tests).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(clear_langgraph_thread(session_id))
    else:
        loop.create_task(clear_langgraph_thread(session_id))
