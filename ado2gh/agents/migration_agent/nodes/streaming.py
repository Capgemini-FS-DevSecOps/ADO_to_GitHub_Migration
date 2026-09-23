"""Token streaming for LLM calls made from the agent graph nodes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from langgraph.config import get_stream_writer

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from ado2gh.agents.migration_agent.runtime.llm_bridge import ModelCapabilities

# ─── Streaming token collection ───────────────────────────────────────

async def _stream_llm_response(
    llm: BaseChatModel,
    messages: list[Any],
    state: dict[str, Any],
    *,
    subagent: str = "orchestrator",
    capabilities: ModelCapabilities | None = None,
) -> str:
    """Stream an LLM response, collecting tokens and emitting live server-sent events (SSE).

    Falls back to ``ainvoke()`` if ``astream()`` is unavailable or raises. Uses
    the stream bus (registered by the orchestrator) to push tokens in real time.

    Args:
        llm: LangChain chat model to call.
        messages: Prompt messages passed to the model unchanged.
        state: Graph state; buffered stream events are appended to it under
            ``_streaming_tokens`` and the session is read from ``session``.
        subagent: Role label attached to every emitted event.
        capabilities: Model capability record; when it reports
            ``supports_thinking`` the reasoning channel is streamed too.

    Returns:
        The concatenated response text, or an empty string when both the
        streaming and the non-streaming call failed.
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
        """Buffer one stream event on the state and push it to the stream bus."""
        # ponytail: individual tokens are not masked — a secret split across two
        # chunks is unmatchable by any regex, and one scan per token would cost
        # more than the stream. The accumulated text is masked where it lands
        # (`_append_event` in the `finally` block below, and the caller's
        # message write), which is the durable/exported copy. Upgrade path if
        # transient SSE fragments must be clean too: buffer to a whole line
        # before emitting, then mask the line.
        state.setdefault("_streaming_tokens", []).append(evt)
        if writer:
            writer(evt)

    start_time = time.time()
    try:
        async for chunk in llm.astream(messages):
            # The langchain stub types .content broadly (multimodal content
            # blocks are possible in principle), but every provider this
            # bridge drives returns plain text chunks for chat completions.
            token = cast("str", chunk.content) if hasattr(chunk, "content") else str(chunk)
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
            from ado2gh.audit import redact_payload

            # See the comment on the streaming branch above: .content is str
            # in practice for every provider this bridge drives.
            full_text = cast("str", result.content) if hasattr(result, "content") else str(result)
            # Non-streaming fallback emits one complete string, so it *can* be masked.
            # redact_payload is a shape-preserving recursive walker (see its
            # docstring/body): dict in -> dict out.
            emit(cast(
                "dict[str, Any]",
                redact_payload({"kind": "token", "content": full_text, "subagent": subagent}),
            ))
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
