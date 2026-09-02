"""Context window management for LangGraph nodes.

Uses LangChain trim_messages with approximate token counting (2026 best practice).
Keeps last 2 PEV cycles full + JSON summary of prior cycles.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately


def _token_counter(messages: list) -> int:
    """Approximate token count for trim_messages (LangChain 2026 helper)."""
    try:
        return count_tokens_approximately(messages)
    except Exception:
        return sum(len(str(getattr(m, "content", m))) for m in messages)


def build_context_with_cycle_summaries(
    messages: list[BaseMessage],
    cycle_summaries: list[dict[str, Any]],
    max_tokens: int,
    *,
    keep_last_cycles: int = 2,
) -> list[BaseMessage]:
    """Build context with PEV cycle summaries using LangChain trim_messages."""
    if not cycle_summaries:
        return trim_messages(
            messages,
            max_tokens=max_tokens,
            strategy="last",
            token_counter=_token_counter,
            include_system=True,
        )

    older = cycle_summaries[:-keep_last_cycles] if len(cycle_summaries) > keep_last_cycles else []
    recent = cycle_summaries[-keep_last_cycles:] if len(cycle_summaries) > keep_last_cycles else cycle_summaries

    summary_parts = []
    if older:
        summary_parts.append(f"Prior PEV cycles (JSON summary):\n{json.dumps(older, default=str)[:2000]}")
    if recent:
        summary_parts.append(f"Recent PEV cycles:\n{json.dumps(recent, default=str)[:3000]}")

    summary_msg = SystemMessage(content="\n\n".join(summary_parts))

    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]
    messages_with_summary = system_msgs + [summary_msg] + non_system

    return trim_messages(
        messages_with_summary,
        max_tokens=max_tokens,
        strategy="last",
        token_counter=_token_counter,
        include_system=True,
    )
