"""LangGraph migration agent — PEV loop with domain subpackages.

Layout:
- ``graph/`` — StateGraph topology + ``AgentState``
- ``nodes/`` — orchestrator, planner, executor, validator node implementations
- ``runtime/`` — turn execution, LLM bridge, context window
- ``session/`` — persistence, activity state machine, lifecycle
- ``hitl/`` — dynamic forms, intake routing, shared schemas
- ``tools/``, ``prompts/`` — per-role tools and instructions
- ``agent.py`` — root graph + optional Google ADK wrappers
"""

from ado2gh.agents.migration_agent.agent import AGENT_NAME, build_app, build_langgraph_agent, get_root_graph
from ado2gh.agents.migration_agent.graph import get_compiled_graph
from ado2gh.agents.migration_agent.graph.state import AgentState
from ado2gh.agents.migration_agent.runtime import (
    ModelCapabilities,
    ModelCapabilityError,
    process_user_message,
    resolve_langchain_llm,
    run_turn,
    stream_turn,
    stream_user_message,
)
from ado2gh.agents.migration_agent.session.state import OrchestratorResult, SessionStateMachine
from ado2gh.agents.migration_agent.session.store import MigrationSessionStore

__all__ = [
    "AGENT_NAME",
    "AgentState",
    "MigrationSessionStore",
    "ModelCapabilities",
    "ModelCapabilityError",
    "OrchestratorResult",
    "SessionStateMachine",
    "build_app",
    "build_langgraph_agent",
    "get_compiled_graph",
    "get_root_graph",
    "process_user_message",
    "resolve_langchain_llm",
    "run_turn",
    "stream_turn",
    "stream_user_message",
]
