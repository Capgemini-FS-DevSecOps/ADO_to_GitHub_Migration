"""LangChain tools for reading the migration knowledge base.

Two read-only tools: find a thing by name, and ask what a change to it would
affect. Both go through the accelerator's ``/v1/knowledge`` endpoints, which
hold the authentication, the role check and the profile scoping — no tool here
opens a database.

Everything these tools return was read out of somebody's pipeline definitions,
so it is untrusted text on its way into a prompt. Each string is masked and
then capped, in that order: capping first can cut a secret shape in half and
leave a fragment too short for any pattern to recognise (CA-003, THR-02-001).
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote, urlencode

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.tools.shared_tools import join_api_path, tool_error
from ado2gh.audit.redaction import redact_text
from ado2gh.knowledge.models import NO_RECORDED_DEPENDENCY_CAVEAT

KNOWLEDGE_PREFIX = "/v1/knowledge"
"""The accelerator route prefix these tools are a client of."""

MAX_TOOL_TEXT_CHARS = 400
"""Longest a single string from the knowledge base may be in a tool result."""

MAX_TOOL_ITEMS = 50
"""Most entries any one list in a tool result may carry into the prompt."""

_VERBATIM_KEYS = ("coverage", "caveats")
"""Keys the cap must not shorten — they are what stop a thin answer reading as a confident one."""

_VERBATIM_TEXT_CHARS = 4000
"""Room for the coverage and the caveats, which this platform writes rather than reads off a pipeline."""

_VERBATIM_ITEMS = 200
"""Room for every caveat a scan can raise, rather than the first fifty."""

_EMPTY_RESULT_NOTE = (
    "An empty result means nothing was recorded within the coverage shown, not "
    "that nothing depends on this thing."
)


def _mask_and_cap(
    value: object,
    depth: int = 0,
    *,
    text_cap: int = MAX_TOOL_TEXT_CHARS,
    item_cap: int = MAX_TOOL_ITEMS,
) -> object:
    """Mask secret shapes in every string of a response, then cap its size.

    Args:
        value: Any part of the decoded response — a string, a list, a mapping
            or a scalar.
        depth: Current nesting depth, used only to stop a pathological response
            from recursing without end.
        text_cap: Longest a single string may be once masked.
        item_cap: Most entries one list may keep.

    Returns:
        The same shape with every string masked and truncated, every list cut
        to ``item_cap``, and anything nested more than ten deep dropped.
    """
    if depth > 10:
        return None
    if isinstance(value, str):
        return redact_text(value)[:text_cap]
    if isinstance(value, dict):
        return {
            str(k): _mask_and_cap(v, depth + 1, text_cap=text_cap, item_cap=item_cap)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _mask_and_cap(v, depth + 1, text_cap=text_cap, item_cap=item_cap)
            for v in value[:item_cap]
        ]
    return value


def _answer(payload: object) -> dict[str, Any]:
    """Turn one accelerator response into a tool result the model can trust.

    Args:
        payload: The decoded response body.

    The coverage and the caveats are the platform's own sentences, not text read
    off somebody's pipeline, and they are the part of the answer a reader needs
    whole: a caveat cut in half, or dropped for being the fifty-first, turns a
    qualified answer into a confident one. They are still masked, but they get
    room the rest of the body does not.

    Returns:
        The masked and capped body, guaranteed to carry a caveat and a plain
        sentence saying what an empty result does and does not prove.
    """
    if not isinstance(payload, dict):
        return {"error": "unexpected_response", "caveats": [NO_RECORDED_DEPENDENCY_CAVEAT]}
    answer: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key)
        answer[name] = (
            _mask_and_cap(value, text_cap=_VERBATIM_TEXT_CHARS, item_cap=_VERBATIM_ITEMS)
            if name in _VERBATIM_KEYS
            else _mask_and_cap(value)
        )
    if not answer.get("caveats"):
        answer["caveats"] = [NO_RECORDED_DEPENDENCY_CAVEAT]
    answer["note"] = _EMPTY_RESULT_NOTE
    return answer


class KnowledgeSearchArgs(BaseModel):
    """Arguments for finding a thing in the knowledge base by name."""

    text: str = Field(description="Name or part of a name to look for, e.g. 'payments-api'")
    limit: int = Field(default=10, description="Maximum matches to return")


class KnowledgeImpactArgs(BaseModel):
    """Arguments for asking what a change to one thing would affect."""

    node_id: str = Field(description="Identifier returned by knowledge_search")
    max_depth: int = Field(default=2, description="Dependency hops to walk, 1 to 4")


def get_knowledge_tools(
    accel_get: Callable[..., Any] | None = None,
    session_token: str | None = None,
) -> list[StructuredTool]:
    """Build the read-only knowledge-base tools.

    Args:
        accel_get: Async callable issuing an authenticated GET against the
            accelerator. ``None`` makes both tools report the accelerator as
            unavailable rather than guessing.
        session_token: Session token forwarded on each call.

    Returns:
        The ``knowledge_search`` and ``knowledge_impact`` tools.
    """

    async def knowledge_search(text: str, limit: int = 10) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            endpoint = f"search?{urlencode({'text': text, 'limit': limit})}"
            resp = await accel_get(
                join_api_path(KNOWLEDGE_PREFIX, endpoint),
                session_token=session_token,
            )
            return _answer(resp)
        except Exception as e:
            return tool_error(e)

    async def knowledge_impact(node_id: str, max_depth: int = 2) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            endpoint = (
                f"nodes/{quote(node_id, safe='')}/impact?{urlencode({'max_depth': max_depth})}"
            )
            resp = await accel_get(
                join_api_path(KNOWLEDGE_PREFIX, endpoint),
                session_token=session_token,
            )
            return _answer(resp)
        except Exception as e:
            return tool_error(e)

    return [
        StructuredTool.from_function(
            coroutine=knowledge_search,
            name="knowledge_search",
            description=(
                "Search the migration knowledge base for a repository, pipeline, feed, "
                "service connection or other thing by name (read-only). Returns each "
                "match with its kind, name and node_id — pass that node_id to "
                "knowledge_impact. The knowledge base holds only what a scan recorded: "
                "no match means nothing matching was scanned, not that the thing does "
                "not exist. The coverage in the response says what was scanned."
            ),
            args_schema=KnowledgeSearchArgs,
        ),
        StructuredTool.from_function(
            coroutine=knowledge_impact,
            name="knowledge_impact",
            description=(
                "Ask what a change to one thing would affect (read-only). Returns its "
                "prerequisites (what it needs in place first), its consumers (what would "
                "notice if it moved), the dependencies it shares with others, the scan "
                "coverage and the caveats. Read the result carefully: an empty list means "
                "nothing was recorded within that coverage, NOT that nothing depends on "
                "the thing, and a dependency marked confidence 'inferred' is a heuristic's "
                "guess that may be wrong, unlike 'declared' or 'resolved'. Say which you "
                "are relying on when you use this in a plan."
            ),
            args_schema=KnowledgeImpactArgs,
        ),
    ]
