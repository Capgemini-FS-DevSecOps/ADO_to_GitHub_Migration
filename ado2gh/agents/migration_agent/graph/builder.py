"""LangGraph StateGraph for the migration agent (Google ADK + LangGraph layout).

Direct plan-execute-validate loop (PEV) flow with strict agent boundaries:

    orchestrator <-> planner -> executor -> validator
                              ^              |
                              +--- retry -----+

Graph topology lives here; node implementations in ``nodes/``. Runtime entry: ``runtime``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from ado2gh.agents.migration_agent.constants import agent_runtime_settings
from ado2gh.agents.migration_agent.graph.state import AgentState
from ado2gh.state.storage_config import CheckpointStorageSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

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

#: Seconds ``_close_checkpointer`` waits for a stale connection to close
#: before abandoning it (the connection's worker thread then leaks instead
#: of hanging the caller).
CHECKPOINTER_CLOSE_TIMEOUT_SECONDS = 2

#: Seconds ``_clear_langgraph_thread_bounded`` waits for a checkpoint clear
#: to finish before abandoning it, so a request-scoped event loop can never
#: be held open by a checkpointer connection that never calls back.
CHECKPOINTER_CLEANUP_TIMEOUT_SECONDS = 5


async def _close_checkpointer(checkpointer: BaseCheckpointSaver) -> None:
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
        await asyncio.wait_for(conn.close(), timeout=CHECKPOINTER_CLOSE_TIMEOUT_SECONDS)
    except Exception:
        logger.debug("stale checkpointer connection close failed", exc_info=True)


async def _get_checkpointer() -> BaseCheckpointSaver | None:
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

    Returns:
        The cached async checkpointer for the running loop —
        ``AsyncPostgresSaver``, ``AsyncSqliteSaver`` or ``InMemorySaver``
        depending on backend and what is installed — or None when no
        checkpointer package is available, in which case thread state is not
        persisted.
    """
    global _CHECKPOINTER, _CHECKPOINTER_LOOP
    current_loop = asyncio.get_running_loop()
    if _CHECKPOINTER is not None:
        if _CHECKPOINTER_LOOP is current_loop:
            return _CHECKPOINTER
        stale, _CHECKPOINTER = _CHECKPOINTER, None
        await _close_checkpointer(stale)
    _CHECKPOINTER_LOOP = current_loop

    settings = CheckpointStorageSettings.from_env()
    if settings.is_postgres():
        try:
            import psycopg
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            db_uri = settings.database_url or settings.postgres_dsn()
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

        db_path = settings.sqlite_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        # aiosqlite's worker thread is non-daemon by default; a connection that
        # gets abandoned (cross-loop reopen, an orphaned cleanup task whose loop
        # tears down first) would otherwise block interpreter/loop shutdown
        # forever if its queued SQLite call never returns. Daemonizing it means
        # a stuck thread can never hold up shutdown, only leak (best we can do
        # without a public daemon option on aiosqlite.connect). aiosqlite.connect()
        # returns the Connection synchronously without starting its thread — the
        # thread only starts once the Connection itself is awaited — so the flag
        # must be set here, before that await, or it raises "cannot set daemon
        # status of active thread".
        conn = aiosqlite.connect(db_path)
        worker_thread = getattr(conn, "_thread", None)
        if worker_thread is not None:
            worker_thread.daemon = True
        conn = await conn
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


#: Retry attempts for a language model or API error raised inside a graph node.
LLM_RETRY_MAX_ATTEMPTS = 3

#: Seconds before the first retry of a language model or API error.
LLM_RETRY_INITIAL_INTERVAL_SECONDS = 0.5

#: Multiplier applied to the retry interval after each further attempt.
LLM_RETRY_BACKOFF_FACTOR = 2.0

# Per LangGraph fault-tolerance docs: retry transient language model and API errors on async nodes.
_LLM_RETRY = RetryPolicy(
    max_attempts=LLM_RETRY_MAX_ATTEMPTS,
    initial_interval=LLM_RETRY_INITIAL_INTERVAL_SECONDS,
    backoff_factor=LLM_RETRY_BACKOFF_FACTOR,
)


def _with_runtime_deps(
    node_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Inject per-invocation callables into node state (not checkpointed).

    Args:
        node_fn: The node coroutine to wrap.

    Returns:
        A node coroutine with the LangGraph-required ``(state, config)`` shape
        that merges the runtime deps in before the call and strips them out of
        the returned update, so they never reach the checkpointer.
    """
    from ado2gh.agents.migration_agent.runtime.deps import (
        merge_runtime_into_state,
        strip_runtime_deps,
    )

    async def wrapped(state: dict[str, Any], _config: RunnableConfig | None = None) -> dict[str, Any]:
        """Run the wrapped node with runtime deps merged in and stripped out.

        ``_config`` is the framework's second argument to a node callable. The
        body never reads it, so it is underscore-prefixed; LangGraph only
        injects the config when the parameter is literally named ``config``.

        Returns:
            The node's ``AgentState`` update with the non-serialisable runtime
            deps removed.
        """
        # strip_runtime_deps is typed to return object because it also passes
        # non-dict values (e.g. a LangGraph Command) through unchanged; every
        # node_fn wrapped here is declared to return dict[str, Any], so the
        # dict branch is always the one taken.
        return cast(
            "dict[str, Any]",
            strip_runtime_deps(await node_fn(merge_runtime_into_state(state))),
        )

    return wrapped


async def _build_graph() -> CompiledStateGraph:
    """Build and compile the migration agent StateGraph.

    Returns:
        The compiled graph, with the checkpointer attached when one is
        available and the human-input interrupt node wired in.
    """
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


# ─── Routing (strict plan-execute-validate loop boundaries) ───────────

def _needs_human_input(state: AgentState) -> bool:
    """Report whether a dynamic form must be collected via LangGraph interrupt().

    Returns:
        True when a form is pending on the state or the session and has not yet
        been submitted; False once a ``form_submission`` is present.
    """
    if state.get("form_submission"):
        return False
    pending = state.get("pending_form")
    if not pending:
        session = state.get("session") or {}
        pending = session.get("pending_form")
    return bool(pending)


def _route_after_orchestrator(state: AgentState) -> str:
    """Orchestrator hands off to planner, executor (approved plan), human input, or finalize.

    Returns:
        The next node name: ``executor`` for an approved plan, ``planner`` when
        work was started without one, ``human_input`` when a form is pending,
        otherwise ``finalize``.
    """
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
    """Planner sends work to executor, orchestrator, human input, or finalize.

    Returns:
        The next node name: ``executor`` once a plan is approved,
        ``orchestrator`` for a clarification or a plan awaiting review,
        ``human_input`` when a form is pending, otherwise ``finalize``.
    """
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

    from ado2gh.agents.migration_agent.hitl.intake import clear_stale_plan_approval

    session = state.get("session") or {}
    # The approval is bound to the plan revision it was given for (THR-09-002).
    # The planner has just run, so the plan in state may be a revision the operator
    # never saw; re-check here instead of trusting the flag, and a revised plan
    # routes back to the orchestrator for a fresh approval rather than to the executor.
    clear_stale_plan_approval(session, plan=state.get("migration_plan"))
    if state.get("migration_plan") and (
        state.get("start_execution") or session.get("plan_approved")
    ):
        return "executor"
    if state.get("migration_plan"):
        return "orchestrator"
    return "finalize"


def _route_after_executor(state: AgentState) -> str:
    """Executor hands results to validator, clarification back to orchestrator, or finalize.

    Returns:
        The next node name: ``validator`` when there is a result to check,
        ``orchestrator`` for a clarification, otherwise ``finalize``.
    """
    if state.get("should_return"):
        return "finalize"
    if state.get("pending_clarification"):
        return "orchestrator"
    if state.get("executor_result"):
        return "validator"
    return "finalize"


def _route_after_validator(state: AgentState) -> str:
    """Route to orchestrator on pass/escalate/exhaustion, planner to retry.

    Returns:
        The next node name: ``orchestrator`` when validation passed, the
        feedback asks for escalation, or the plan-execute-validate loop's retry / iteration ceiling is
        reached; ``planner`` to replan; ``finalize`` when the turn is over.
    """
    if state.get("should_return"):
        return "finalize"
    validation_result = state.get("validation_result") or {}
    if validation_result.get("passed"):
        return "orchestrator"
    runtime_settings = agent_runtime_settings()
    iteration = int(state.get("iteration") or 0)
    max_iterations = int(state.get("max_iterations") or runtime_settings.max_iterations)
    retry_count = int(state.get("pev_retry_count") or 0)
    feedback = state.get("validation_feedback") or {}
    if (
        feedback.get("escalate")
        or retry_count >= runtime_settings.max_pev_retries
        or iteration >= max_iterations
    ):
        return "orchestrator"
    return "planner"


# Legacy alias for tests that still import the old name
_route_after_execute_tools = _route_after_orchestrator


# ─── Compiled graph singleton ─────────────────────────────────────────

_compiled_graph: Any = None
_compiled_graph_loop: Any = None


async def get_compiled_graph() -> CompiledStateGraph:
    """Return the compiled graph, building it on first call.

    Rebuilds when the running event loop changed — the compiled graph holds a
    direct reference to the checkpointer's connection, which is loop-bound
    (see ``_get_checkpointer``).

    Returns:
        The compiled graph for the running event loop.
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


async def _clear_langgraph_thread_bounded(session_id: str) -> None:
    """Run ``clear_langgraph_thread`` with a hard cap so a detached task
    can never outlive the loop it was scheduled on.

    ``clear_langgraph_thread_sync``'s fire-and-forget branch schedules this
    on whatever loop happens to be running. A request-scoped loop (a bare
    ``TestClient(app).post()`` opens and tears down a fresh one per call, per
    Starlette's ``_portal_factory``) can close before this task finishes,
    leaving it waiting forever on a checkpointer connection whose non-daemon
    aiosqlite worker thread never calls back — which wedges that loop's
    shutdown. Bounding the wait lets the task self-terminate either way.
    """
    try:
        await asyncio.wait_for(
            clear_langgraph_thread(session_id), timeout=CHECKPOINTER_CLEANUP_TIMEOUT_SECONDS
        )
    except Exception:
        logger.debug("bounded checkpoint clear did not finish", exc_info=True)


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
        loop.create_task(_clear_langgraph_thread_bounded(session_id))
