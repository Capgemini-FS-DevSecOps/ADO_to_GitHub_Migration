"""Root agent definition — Google ADK layout over LangGraph PEV.

Industry-standard agent projects expose a single ``root_agent`` (or graph)
entry point. This migration agent keeps **LangGraph** as the orchestration
engine because nodes share rich ``AgentState`` (session, LLM, plans, forms).

ADK's ``LangGraphAgent`` wrapper only forwards ``messages``; we therefore
export the compiled LangGraph as ``root_graph``. Turn execution is in ``runtime``.
Optional ADK ``App`` wrapper is ``build_app()`` below.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ado2gh.agents.migration_agent.graph import get_compiled_graph

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

AGENT_NAME = "migration_agent"
AGENT_DESCRIPTION = (
    "Enterprise ADO → GitHub migration agent. PEV loop: orchestrator ↔ planner "
    "→ executor → validator. Dry-run by default; live execution requires approval."
)

# ADK / `adk run` convention — compiled LangGraph workflow.
root_graph: CompiledStateGraph | None = None


def get_root_graph() -> CompiledStateGraph:
    """Return the compiled LangGraph (lazy singleton).

    Sync entry point for ADK tooling only (production uses ``runtime.run_turn``,
    fully async) — bridges via ``asyncio.run`` since ``get_compiled_graph`` is async.

    Returns:
        The compiled LangGraph, built on first call and cached in the module
        global ``root_graph`` thereafter.
    """
    global root_graph
    if root_graph is None:
        root_graph = asyncio.run(get_compiled_graph())
    return root_graph


def build_langgraph_agent() -> object:
    """Build ADK LangGraphAgent when google-adk is installed (messages-only graphs).

    Not used for the full migration workflow — kept for ADK tooling compatibility
    and smoke tests. Production execution uses ``runtime.run_turn``.

    Returns:
        A ``google.adk.agents.langgraph_agent.LangGraphAgent`` wrapping the
        compiled graph. Typed ``object`` because google-adk is an optional
        extra and its types are not importable without it.

    Raises:
        ImportError: google-adk is not installed.
    """
    try:
        from google.adk.agents.langgraph_agent import LangGraphAgent
    except ImportError as exc:
        raise ImportError(
            "google-adk is required for build_langgraph_agent(); "
            'install with pip install "ado2gh[agent]"'
        ) from exc

    return LangGraphAgent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        graph=get_root_graph(),
        instruction="",
    )


def build_app() -> object:
    """Create a Google ADK ``App`` around the migration LangGraph agent.

    Returns:
        A ``google.adk.apps.app.App`` named ``ado2gh_migration``. Typed
        ``object`` because google-adk is an optional extra and its types are
        not importable without it.

    Raises:
        ImportError: google-adk is not installed.
    """
    try:
        from google.adk.apps.app import App
    except ImportError as exc:
        raise ImportError(
            'google-adk is required; install with pip install "ado2gh[agent]"'
        ) from exc

    return App(
        name="ado2gh_migration",
        root_agent=build_langgraph_agent(),
    )
