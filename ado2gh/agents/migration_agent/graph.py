"""LangGraph StateGraph for the migration agent.

Direct PEV flow with strict agent boundaries:

    orchestrator <-> planner -> executor -> validator
                              ^              |
                              +--- retry -----+

Communication rules:
- orchestrator ↔ planner only (user-facing updates via orchestrator)
- planner → executor when plan is approved
- executor → validator only
- validator → planner only (planner decides retry, queue, or completion)
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, END

from ado2gh.agents.migration_agent.state import AgentState
from ado2gh.agents.migration_agent.constants import GRAPH_RECURSION_LIMIT

# Core graph nodes
NODE_ORCHESTRATOR = "orchestrator"
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_VALIDATOR = "validator"
NODE_FINALIZE = "finalize"

ALL_NODES = (
    NODE_ORCHESTRATOR,
    NODE_PLANNER,
    NODE_EXECUTOR,
    NODE_VALIDATOR,
    NODE_FINALIZE,
)

# Legacy aliases (tests / streaming may reference these)
NODE_CLASSIFY_INTENT = NODE_ORCHESTRATOR
NODE_EXECUTE_TOOLS = NODE_ORCHESTRATOR


def _get_checkpointer():
    """Build a LangGraph checkpointer based on storage backend."""
    import os
    backend = os.environ.get("ADO2GH_STORAGE_BACKEND", "sqlite").lower()
    if backend == "postgresql":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            import os as _os
            conn_info = {
                "host": _os.environ.get("PGHOST", "localhost"),
                "port": int(_os.environ.get("PGPORT", 5432)),
                "user": _os.environ.get("PGUSER", "ado2gh"),
                "password": _os.environ.get("PGPASSWORD", ""),
                "dbname": _os.environ.get("PGDATABASE", "ado2gh"),
            }
            return PostgresSaver.from_conn_info(**conn_info)
        except ImportError:
            pass
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import os as _os
        db_path = _os.environ.get("ADO2GH_SQLITE_PATH", "data/agent_checkpoints.db")
        return SqliteSaver.from_conn_string(db_path)
    except ImportError:
        return None


def _build_graph() -> Any:
    """Build and compile the migration agent StateGraph."""
    from ado2gh.agents.migration_agent.nodes import (
        orchestrator_node,
        planner_node,
        executor_node,
        validator_node,
        finalize_node,
    )

    graph = StateGraph(AgentState)

    graph.add_node(NODE_ORCHESTRATOR, orchestrator_node)
    graph.add_node(NODE_PLANNER, planner_node)
    graph.add_node(NODE_EXECUTOR, executor_node)
    graph.add_node(NODE_VALIDATOR, validator_node)
    graph.add_node(NODE_FINALIZE, finalize_node)

    graph.set_entry_point(NODE_ORCHESTRATOR)

    graph.add_conditional_edges(
        NODE_ORCHESTRATOR,
        _route_after_orchestrator,
        {
            "planner": NODE_PLANNER,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_conditional_edges(
        NODE_PLANNER,
        _route_after_planner,
        {
            "orchestrator": NODE_ORCHESTRATOR,
            "executor": NODE_EXECUTOR,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_conditional_edges(
        NODE_EXECUTOR,
        _route_after_executor,
        {
            "validator": NODE_VALIDATOR,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_conditional_edges(
        NODE_VALIDATOR,
        _route_after_validator,
        {
            "planner": NODE_PLANNER,
            "finalize": NODE_FINALIZE,
        },
    )

    graph.add_edge(NODE_FINALIZE, END)

    checkpointer = _get_checkpointer()
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


# ─── Routing (strict PEV boundaries) ──────────────────────────────────

def _route_after_orchestrator(state: AgentState) -> str:
    """Orchestrator only hands off to planner or ends the turn."""
    if state.get("should_return"):
        return "finalize"
    if state.get("start_execution") or state.get("start_pev"):
        return "planner"
    return "finalize"


def _route_after_planner(state: AgentState) -> str:
    """Planner sends work to executor or user updates to orchestrator."""
    if state.get("should_return"):
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
    """Executor only hands results to validator."""
    if state.get("should_return"):
        return "finalize"
    if state.get("executor_result"):
        return "validator"
    return "finalize"


def _route_after_validator(state: AgentState) -> str:
    """Validator always hands off to planner."""
    if state.get("should_return"):
        return "finalize"
    return "planner"


# Legacy alias for tests that still import the old name
_route_after_execute_tools = _route_after_orchestrator


# ─── Compiled graph singleton ─────────────────────────────────────────

_compiled_graph: Any = None


def get_compiled_graph() -> Any:
    """Return the compiled graph, building it on first call."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_graph()
    return _compiled_graph


def reset_compiled_graph() -> None:
    """Reset the compiled graph (useful for tests)."""
    global _compiled_graph
    _compiled_graph = None


def clear_langgraph_thread(session_id: str) -> None:
    """Delete LangGraph checkpoint state for a session thread."""
    thread_id = str(session_id or "").strip()
    if not thread_id:
        return
    try:
        graph = get_compiled_graph()
        checkpointer = getattr(graph, "checkpointer", None)
        if checkpointer is not None and hasattr(checkpointer, "delete_thread"):
            checkpointer.delete_thread({"configurable": {"thread_id": thread_id}})
    except Exception:
        pass
