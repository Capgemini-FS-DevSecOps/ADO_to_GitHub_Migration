"""LangGraph state definition for the migration agent.

Defines the shared state that flows through the graph. Uses reducers for
accumulating list fields (operator.add) and add_messages for message history.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """State that flows through the LangGraph migration agent."""

    # LangChain message history (accumulates via add_messages reducer)
    messages: Annotated[list[BaseMessage], add_messages]

    # The user's original message for this turn
    user_message: str

    # Session reference — the live session dict from _sessions
    session: dict[str, Any]

    # LLM config. The client itself, the accelerator callables, build_plan,
    # session_token and capabilities are deliberately NOT declared here: they are
    # not serializable into a checkpoint, so they travel per-invocation via
    # contextvars (runtime/deps.py) and are stripped from every node's return.
    llm_degraded: bool
    llm_unconfigured: bool

    # Intent classification result
    intent: str  # general_chat | migration_info | migration_action

    # LLM output for current iteration
    thinking: str | None
    reply: str | None
    tool_calls: list[dict[str, Any]]
    parsed: dict[str, Any]

    # Iteration control
    iteration: int
    max_iterations: int
    accumulated_tokens: int
    max_token_budget: int

    # PEV-related fields
    start_pev: bool
    start_execution: bool
    pending_form: dict[str, Any] | None
    form_submission: dict[str, Any] | None  # T101: Handle form submissions (e.g., rollback)
    should_return: bool
    error: str | None
    pev_active: bool
    pev_retry_count: int
    inter_agent_messages: Annotated[list[dict[str, Any]], operator.add]
    migration_plan: dict[str, Any] | None
    executor_result: dict[str, Any] | None
    validation_result: dict[str, Any] | None
    validation_feedback: dict[str, Any] | None
    planner_next: str | None  # orchestrator | executor — set after validator handoff
    pending_clarification: dict[str, Any] | None
    cycle_summaries: Annotated[list[dict[str, Any]], operator.add]
    migration_queue: dict[str, Any] | None
    rollback_records: Annotated[list[dict[str, Any]], operator.add]
    streaming_tokens: Annotated[list[dict[str, Any]], operator.add]
    message_queue: Annotated[list[str], operator.add]
