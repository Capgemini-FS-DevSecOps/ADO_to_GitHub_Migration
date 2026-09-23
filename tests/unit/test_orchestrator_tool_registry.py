"""ORCHESTRATOR_TOOL_REGISTRY must name exactly the tools the dispatcher handles.

`tools/orchestrator_tools.py` builds the orchestrator's tool set;
`nodes/orchestrator_tools.py` dispatches tool calls by name a second time.
Nothing ties the two lists together, so a name added or removed on one side
can silently stop matching the other. This test reads the dispatcher's
`tool_name == "..."` branches straight from its source and checks the set
against the registry, so a drift fails the build instead of failing quietly
at run time.
"""
import re
from pathlib import Path

from ado2gh.agents.migration_agent.tools.orchestrator_tools import (
    ORCHESTRATOR_TOOL_REGISTRY,
)

_DISPATCHER_PATH = (
    Path(__file__).resolve().parents[2]
    / "ado2gh"
    / "agents"
    / "migration_agent"
    / "nodes"
    / "orchestrator_tools.py"
)


_BRANCH_RE = re.compile(r'^\s*(?:if|elif)\s+tool_name == "(\w+)"', re.MULTILINE)


def _dispatched_tool_names() -> set[str]:
    """Extract every `if`/`elif tool_name == "..."` dispatch branch name from the dispatcher source.

    Anchored to the start of an `if`/`elif` line so an unrelated `tool_name ==
    "..."` comparison used elsewhere (for example inside a boolean expression
    that only decides whether to skip telemetry) is not mistaken for a
    dispatch branch.
    """
    source = _DISPATCHER_PATH.read_text(encoding="utf-8")
    return set(_BRANCH_RE.findall(source))


def test_registry_names_match_dispatcher_branches():
    dispatched = _dispatched_tool_names()
    assert dispatched, "no tool_name branches found — dispatcher file may have moved"
    assert set(ORCHESTRATOR_TOOL_REGISTRY.keys()) == dispatched


def test_registry_builders_produce_tools_named_after_their_key():
    for name, builder in ORCHESTRATOR_TOOL_REGISTRY.items():
        if builder is None:
            continue
        tool = builder(None, None)
        assert tool.name == name
