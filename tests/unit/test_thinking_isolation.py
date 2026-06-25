"""Thinking log isolation per user turn."""
from __future__ import annotations

from ado2gh.agents.migration_agent.route_helpers import (
    _prune_stale_thinking_events,
    _thinking_log_for_current_turn,
)


def test_prune_stale_thinking_events_keeps_current_turn_only():
    session = {
        "messages": [
            {"role": "user", "content": "migrate a repo", "kind": "message"},
            {"role": "system", "content": "thought about migrate a repo", "kind": "thinking", "subagent": "orchestrator"},
            {"role": "user", "content": "Migrate another", "kind": "message"},
        ],
    }
    _prune_stale_thinking_events(session)
    assert session["messages"] == [
        {"role": "user", "content": "migrate a repo", "kind": "message"},
        {"role": "user", "content": "Migrate another", "kind": "message"},
    ]


def test_thinking_log_for_current_turn_excludes_prior_turn():
    messages = [
        {"role": "user", "content": "migrate a repo", "kind": "message"},
        {"role": "system", "content": "old thought", "kind": "thinking", "subagent": "orchestrator"},
        {"role": "user", "content": "Migrate another", "kind": "message"},
        {"role": "system", "content": "new thought", "kind": "thinking", "subagent": "orchestrator"},
        {"role": "system", "content": "planning", "kind": "status", "subagent": "planner"},
    ]
    log = _thinking_log_for_current_turn(messages)
    assert len(log) == 2
    assert log[0]["content"] == "new thought"
    assert log[1]["content"] == "planning"
