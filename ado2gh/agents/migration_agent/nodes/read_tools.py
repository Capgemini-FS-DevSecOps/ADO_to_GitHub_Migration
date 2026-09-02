"""Read-only tool execution via LangGraph ToolNode (2026 best practice).

Planner/validator research tools are read-only; writes stay in executor with guardrails.
Falls back to direct StructuredTool.ainvoke when ToolNode lacks graph runtime config.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode


async def _invoke_tool_direct(tool: Any, args: dict[str, Any]) -> Any:
    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(args)
    if hasattr(tool, "coroutine") and tool.coroutine:
        return await tool.coroutine(**args)
    if hasattr(tool, "invoke"):
        return tool.invoke(args)
    raise TypeError(f"Tool {getattr(tool, 'name', tool)!r} is not async-invokable")


async def invoke_read_tools(
    tool_calls: list[dict[str, Any]],
    tools: list[Any],
    *,
    existing_messages: list[Any] | None = None,
) -> list[ToolMessage]:
    """Execute read-only tool calls through LangGraph ToolNode or direct ainvoke."""
    if not tool_calls or not tools:
        return []

    normalized = []
    for tc in tool_calls:
        normalized.append(
            {
                "id": tc.get("id") or tc.get("tool_call_id") or f"call_{tc.get('name', 'tool')}",
                "name": tc.get("name", ""),
                "args": tc.get("arguments") or tc.get("args") or {},
            }
        )

    tools_by_name = {getattr(t, "name", ""): t for t in tools}
    messages = list(existing_messages or [])
    messages.append(AIMessage(content="", tool_calls=normalized))

    try:
        tool_node = ToolNode(tools)
        result = await tool_node.ainvoke(
            {"messages": messages},
            config=RunnableConfig(),
        )
        out_messages = result.get("messages") if isinstance(result, dict) else []
        tool_messages = [m for m in out_messages if isinstance(m, ToolMessage)]
        if tool_messages:
            return tool_messages
    except (ValueError, KeyError, TypeError):
        pass

    out: list[ToolMessage] = []
    for tc in normalized:
        tool = tools_by_name.get(tc["name"])
        if not tool:
            out.append(
                ToolMessage(
                    content=json.dumps({"error": "tool_unavailable"}),
                    name=tc["name"],
                    tool_call_id=tc["id"],
                )
            )
            continue
        try:
            raw = await _invoke_tool_direct(tool, tc["args"])
            content = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        except Exception as exc:
            content = json.dumps({"error": str(exc)})
        out.append(
            ToolMessage(
                content=content,
                name=tc["name"],
                tool_call_id=tc["id"],
            )
        )
    return out
