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


def set_runtime_deps(**deps: object) -> None:
    """Bind callables/models for the current graph invocation.

    Keys outside ``_RUNTIME_KEYS`` are dropped, so callers may pass a wider
    mapping without leaking unexpected values into node state.
    """
    _RUNTIME.set({k: v for k, v in deps.items() if k in _RUNTIME_KEYS})


def clear_runtime_deps() -> None:
    """Unbind the current invocation's deps, leaving the contextvar empty."""
    _RUNTIME.set({})


def get_runtime_deps() -> dict[str, Any]:
    """Read the deps bound for the current invocation.

    Returns:
        A copy of the bound deps mapping, empty when nothing is bound. Copying
        keeps callers from mutating the value held by the contextvar.
    """
    return dict(_RUNTIME.get({}))


def merge_runtime_into_state(state: dict[str, Any]) -> dict[str, Any]:
    """Overlay runtime deps onto state for node execution (graph path only).

    Returns:
        ``state`` itself when nothing is bound, otherwise a copy with every
        non-None dep merged in under its ``_RUNTIME_KEYS`` name.
    """
    deps = get_runtime_deps()
    if not deps:
        return state
    merged = dict(state)
    for key, value in deps.items():
        if value is not None:
            merged[key] = value
    return merged


def strip_runtime_deps(update: object) -> object:
    """Drop runtime-only keys from a node's state update before it is checkpointed.

    ``merge_runtime_into_state`` puts a live LLM client and raw callables into the
    dict handed to each node, so a node that returns ``{**state, ...}`` would push
    non-serializable objects into the checkpoint. Stripping here — the one place
    every node's return value passes through — keeps that impossible regardless of
    how any individual node builds its update.

    Returns:
        The update without any ``_RUNTIME_KEYS`` entry when it is a dict; any
        other value (e.g. a LangGraph ``Command``) is passed through unchanged.
    """
    if not isinstance(update, dict):
        return update
    return {k: v for k, v in update.items() if k not in _RUNTIME_KEYS}
