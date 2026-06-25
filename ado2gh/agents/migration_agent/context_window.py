"""Context window management for LangGraph nodes.

Uses LangChain trim_messages to manage context within token budget.
Keeps last 2 PEV cycles full + JSON summary of prior cycles.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage, trim_messages


def build_context_with_cycle_summaries(
    messages: list[BaseMessage],
    cycle_summaries: list[dict[str, Any]],
    max_tokens: int,
    *,
    keep_last_cycles: int = 2,
) -> list[BaseMessage]:
    """Build context with PEV cycle summaries using LangChain trim_messages.

    Keeps last `keep_last_cycles` full cycle data + JSON summary of prior cycles.
    Uses LangChain's built-in trim_messages for token-aware message trimming.
    """
    if not cycle_summaries:
        # No summaries — trim messages directly
        return trim_messages(
            messages,
            max_tokens=max_tokens,
            strategy="last",
            token_counter=len,  # Simple char-based fallback
        )

    # Build summary of older cycles
    older = cycle_summaries[:-keep_last_cycles] if len(cycle_summaries) > keep_last_cycles else []
    recent = cycle_summaries[-keep_last_cycles:] if len(cycle_summaries) > keep_last_cycles else cycle_summaries

    import json

    summary_parts = []
    if older:
        summary_parts.append(f"Prior PEV cycles (JSON summary):\n{json.dumps(older, default=str)[:2000]}")
    if recent:
        summary_parts.append(f"Recent PEV cycles:\n{json.dumps(recent, default=str)[:3000]}")

    summary_msg = SystemMessage(content="\n\n".join(summary_parts))

    # Insert summary before the recent messages
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]

    # Build messages with summary
    messages_with_summary = system_msgs + [summary_msg] + non_system

    # Use LangChain trim_messages to fit within budget
    return trim_messages(
        messages_with_summary,
        max_tokens=max_tokens,
        strategy="last",
        token_counter=len,  # Simple char-based fallback
    )
