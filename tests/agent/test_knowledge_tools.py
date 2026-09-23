"""The agent's two knowledge-base tools: which path they call, and what they hand the model.

Nothing here reaches the network. The accelerator is a callable double that
records the path it was asked for and answers with an obviously fake payload.

Three properties matter enough to be pinned:

* the tools call the accelerator's ``/v1/knowledge`` routes and never a database;
* untrusted text out of somebody's pipeline definitions is masked and *then*
  capped, so a cap can never split a secret shape into an unrecognisable
  fragment (CA-003);
* an empty answer always arrives with the caveat and the plain sentence saying
  that nothing was *recorded*, which is not the same claim as nothing existing.
"""
from __future__ import annotations

import pytest

from ado2gh.agents.migration_agent.tools.knowledge_tools import (
    MAX_TOOL_ITEMS,
    MAX_TOOL_TEXT_CHARS,
    get_knowledge_tools,
)
from ado2gh.knowledge.models import NO_RECORDED_DEPENDENCY_CAVEAT

# Obviously fake: the shape of a GitHub token, never a real one.
FAKE_TOKEN = "ghp_" + "a" * 36


class _Accel:
    """An accelerator double that records its calls and returns a fixed payload."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.paths: list[str] = []

    async def __call__(self, path: str, session_token: str | None = None) -> object:
        self.paths.append(path)
        self.token = session_token
        return self.payload


def _tools(payload: object) -> tuple[dict, _Accel]:
    """Build the two tools against one accelerator double.

    Args:
        payload: What the double answers every call with.

    Returns:
        The tools by name, and the double.
    """
    accel = _Accel(payload)
    tools = get_knowledge_tools(accel_get=accel, session_token="fake-session-token")
    return {t.name: t for t in tools}, accel


def test_both_tools_are_built_with_schemas_and_descriptions():
    """Both tools exist, take arguments and explain themselves to the model."""
    tools, _ = _tools({})
    assert set(tools) == {"knowledge_search", "knowledge_impact"}
    for tool in tools.values():
        assert tool.args_schema is not None
        assert len(tool.description) > 10


def test_impact_description_warns_about_empty_and_inferred_results():
    """The model is told what an empty list and an inferred edge each mean."""
    tools, _ = _tools({})
    description = tools["knowledge_impact"].description
    assert "NOT that nothing depends" in description
    assert "inferred" in description


async def test_search_calls_the_knowledge_search_route():
    """knowledge_search goes through the accelerator route, carrying its arguments."""
    tools, accel = _tools({"results": [], "coverage": {"projects_scanned": 2}, "caveats": ["fake caveat"]})
    await tools["knowledge_search"].ainvoke({"text": "payments-api", "limit": 5})
    assert accel.paths == ["/v1/knowledge/search?text=payments-api&limit=5"]
    assert accel.token == "fake-session-token"


async def test_impact_calls_the_impact_route_with_an_encoded_identifier():
    """A node identifier chosen by the model stays inside the knowledge prefix."""
    tools, accel = _tools({"subject": None, "consumers": [], "coverage": {}, "caveats": []})
    await tools["knowledge_impact"].ainvoke({"node_id": "proj/repo id", "max_depth": 3})
    assert accel.paths == ["/v1/knowledge/nodes/proj%2Frepo%20id/impact?max_depth=3"]


async def test_untrusted_text_is_masked_before_it_is_capped():
    """A token in scanned evidence is masked, and the masking runs before the cap.

    The long field is built so the token sits well past ``MAX_TOOL_TEXT_CHARS``:
    if the cap ran first the token would be gone, and with it the proof that
    masking saw the whole string.
    """
    long_evidence = ("x" * (MAX_TOOL_TEXT_CHARS + 50)) + " " + FAKE_TOKEN
    tools, _ = _tools({
        "consumers": [{"node": {"name": f"repo {FAKE_TOKEN}"}, "evidence": [{"step": long_evidence}]}],
        "coverage": {},
        "caveats": ["fake caveat"],
    })
    answer = await tools["knowledge_impact"].ainvoke({"node_id": "n1"})
    rendered = str(answer)
    assert FAKE_TOKEN not in rendered
    assert "ghp_***" in rendered
    assert len(answer["consumers"][0]["evidence"][0]["step"]) <= MAX_TOOL_TEXT_CHARS


async def test_long_lists_are_capped():
    """A knowledge base with thousands of consumers cannot flood the prompt."""
    tools, _ = _tools({"consumers": [{"node": {"name": f"r{i}"}} for i in range(MAX_TOOL_ITEMS + 25)]})
    answer = await tools["knowledge_impact"].ainvoke({"node_id": "n1"})
    assert len(answer["consumers"]) == MAX_TOOL_ITEMS


async def test_an_empty_answer_says_unrecorded_not_absent():
    """No consumers plus no caveats from the route still reaches the model as 'not recorded'."""
    tools, _ = _tools({"subject": None, "consumers": [], "prerequisites": [], "coverage": {}, "caveats": []})
    answer = await tools["knowledge_impact"].ainvoke({"node_id": "never-scanned"})
    assert answer["consumers"] == []
    assert answer["caveats"] == [NO_RECORDED_DEPENDENCY_CAVEAT]
    assert "not that nothing depends on this thing" in answer["note"]


async def test_the_coverage_and_the_caveats_are_not_shortened_by_the_cap():
    """A long caveat, or the sixtieth one, still reaches the model whole.

    Capping these the way untrusted text is capped would turn a qualified answer
    into a confident one, which is exactly what the caveats exist to prevent.
    """
    long_caveat = "Seventeen pipeline definitions could not be parsed. " * 20
    tools, _ = _tools({
        "consumers": [],
        "coverage": {"note": "x" * (MAX_TOOL_TEXT_CHARS + 200)},
        "caveats": [long_caveat] + [f"caveat {i}" for i in range(MAX_TOOL_ITEMS + 20)],
    })
    answer = await tools["knowledge_impact"].ainvoke({"node_id": "n1"})
    assert answer["caveats"][0] == long_caveat
    assert len(answer["caveats"]) == MAX_TOOL_ITEMS + 21
    assert len(answer["coverage"]["note"]) == MAX_TOOL_TEXT_CHARS + 200


async def test_caveats_from_the_route_are_passed_through():
    """The route's own caveats are what the model sees; the standing one is a fallback."""
    tools, _ = _tools({"results": [], "coverage": {}, "caveats": ["one project was unreadable"]})
    answer = await tools["knowledge_search"].ainvoke({"text": "anything"})
    assert answer["caveats"] == ["one project was unreadable"]


@pytest.mark.parametrize("tool_name,args", [
    ("knowledge_search", {"text": "a"}),
    ("knowledge_impact", {"node_id": "n1"}),
])
async def test_without_an_accelerator_the_tools_say_so(tool_name, args):
    """No accelerator means no answer — never a guess."""
    tools = {t.name: t for t in get_knowledge_tools(accel_get=None)}
    assert (await tools[tool_name].ainvoke(args))["error"] == "accelerator_unavailable"


async def test_a_failing_accelerator_becomes_a_tool_error():
    """An exception is reported as a masked tool error, not raised into the graph."""
    async def boom(path: str, session_token: str | None = None) -> object:
        raise RuntimeError(f"upstream refused with {FAKE_TOKEN}")

    tools = {t.name: t for t in get_knowledge_tools(accel_get=boom)}
    answer = await tools["knowledge_search"].ainvoke({"text": "a"})
    assert answer["error"] == "RuntimeError"
    assert FAKE_TOKEN not in answer["detail"]


def test_the_planner_binds_both_tools():
    """The planner is the role that needs them, so they are on its tool list."""
    from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools

    names = {t.name for t in get_planner_tools()}
    assert {"knowledge_search", "knowledge_impact"} <= names


def test_both_tools_are_classified_as_reads():
    """An unclassified tool is blocked outright, so the classification is pinned here too."""
    from ado2gh.agents.migration_agent.guardrails import _READ_OPERATIONS

    assert {"knowledge_search", "knowledge_impact"} <= _READ_OPERATIONS
