"""streaming.py module."""
from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

# ─── Streaming token collection ───────────────────────────────────────

async def _stream_llm_response(
    llm: Any,
    messages: list,
    state: dict[str, Any],
    *,
    subagent: str = "orchestrator",
    capabilities: Any = None,
) -> str:
    """Stream LLM response, collecting tokens and emitting live SSE events.

    Falls back to invoke() if astream() is unavailable or raises.
    Uses the stream bus (registered by the orchestrator) to push tokens in real-time.
    """
    import time

    from ado2gh.agents.metrics import get_metrics_collector

    metrics = get_metrics_collector()
    full_text = ""
    thinking_buffer = ""
    session = state.get("session") or {}
    supports_thinking = getattr(capabilities, "supports_thinking", False) if capabilities else False

    writer = None
    try:
        writer = get_stream_writer()
    except RuntimeError:
        pass

    def emit(evt: dict[str, Any]) -> None:
        state.setdefault("_streaming_tokens", []).append(evt)
        if writer:
            writer(evt)

    start_time = time.time()
    try:
        async for chunk in llm.astream(messages):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            if token:
                full_text += token
                emit({"kind": "token", "content": token, "subagent": subagent})
            if supports_thinking and hasattr(chunk, "additional_kwargs"):
                thinking = chunk.additional_kwargs.get("thinking") or chunk.additional_kwargs.get("reasoning")
                if thinking:
                    thinking_buffer += str(thinking)
                    emit({"kind": "thinking", "content": thinking, "subagent": subagent})
            if supports_thinking and hasattr(chunk, "response_metadata"):
                meta = chunk.response_metadata or {}
                thinking = meta.get("thinking") or meta.get("reasoning_content")
                if thinking:
                    emit({"kind": "thinking", "content": thinking, "subagent": subagent})
    except (AttributeError, NotImplementedError, Exception):
        try:
            result = await llm.ainvoke(messages)
            full_text = result.content if hasattr(result, "content") else str(result)
            emit({"kind": "token", "content": full_text, "subagent": subagent})
        except Exception:
            full_text = ""
        else:
            if supports_thinking and hasattr(result, "additional_kwargs"):
                thinking = result.additional_kwargs.get("thinking") or result.additional_kwargs.get("reasoning")
                if thinking:
                    emit({"kind": "thinking", "content": thinking, "subagent": subagent})
    finally:
        duration = time.time() - start_time
        metrics.record_llm_call(duration)
        if thinking_buffer.strip() and session:
            from ado2gh.agents.migration_agent.utils import _append_event

            buffered = thinking_buffer.strip()
            recent = session.get("messages", [])[-3:]
            already = any(
                m.get("kind") == "thinking"
                and m.get("content") == buffered
                and m.get("subagent") == subagent
                for m in recent
            )
            if not already:
                _append_event(
                    session,
                    role="system",
                    content=buffered,
                    kind="thinking",
                    subagent=subagent,
                )

    return full_text
