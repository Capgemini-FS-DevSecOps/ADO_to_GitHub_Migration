"""Per-invocation runtime dependencies (not checkpointed).

LangGraph checkpointers require JSON/msgpack-serializable state. LLM clients and
HTTP callables must live outside AgentState — injected via contextvars for each
graph invoke (see LangGraph persistence docs / issue #2135).
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_RUNTIME: ContextVar[dict[str, Any]] = ContextVar("migration_agent_runtime", default={})

_RUNTIME_KEYS = frozenset({
    "llm",
    "accel_get",
    "accel_post",
    "build_plan",
    "session_token",
    "capabilities",
})


def set_runtime_deps(**deps: Any) -> None:
    """Bind callables/models for the current graph invocation."""
    _RUNTIME.set({k: v for k, v in deps.items() if k in _RUNTIME_KEYS})


def clear_runtime_deps() -> None:
    _RUNTIME.set({})


def get_runtime_deps() -> dict[str, Any]:
    return dict(_RUNTIME.get({}))


def merge_runtime_into_state(state: dict[str, Any]) -> dict[str, Any]:
    """Overlay runtime deps onto state for node execution (graph path only)."""
    deps = get_runtime_deps()
    if not deps:
        return state
    merged = dict(state)
    for key, value in deps.items():
        if value is not None:
            merged[key] = value
    return merged


def strip_runtime_deps(update: Any) -> Any:
    """Drop runtime-only keys from a node's state update before it is checkpointed.

    ``merge_runtime_into_state`` puts a live LLM client and raw callables into the
    dict handed to each node, so a node that returns ``{**state, ...}`` would push
    non-serializable objects into the checkpoint. Stripping here — the one place
    every node's return value passes through — keeps that impossible regardless of
    how any individual node builds its update.
    """
    if not isinstance(update, dict):
        return update
    return {k: v for k, v in update.items() if k not in _RUNTIME_KEYS}
