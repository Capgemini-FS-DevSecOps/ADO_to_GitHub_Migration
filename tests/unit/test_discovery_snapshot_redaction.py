"""Tests that the discovery snapshot is masked before it enters session state (CA-003).

Commit bf1a07c masked the accelerator's discovery response in
``nodes/planner_research.py``; two other paths stored the same kind of
response into session state without going through that masking:
``utils.py::load_discovery_snapshot`` and the planner node's own inline
discovery fetch (``nodes/planner.py``). Both route through the existing
``redact_payload`` helper now, so neither one needs a masking helper of its
own.
"""
import json
from unittest.mock import AsyncMock

import pytest

from ado2gh.agents.migration_agent.nodes import planner_node
from ado2gh.agents.migration_agent.utils import load_discovery_snapshot


def _make_state(**kwargs):
    session = kwargs.pop("session", {})
    defaults = {
        "session": session,
        "llm": None,
        "llm_unconfigured": True,
        "capabilities": None,
        "accel_get": None,
        "session_token": None,
        "validation_feedback": None,
        "iteration": 0,
        "migration_plan": None,
    }
    defaults.update(kwargs)
    return defaults


_LEAKED_TOKEN = "ghp_" + "a" * 36


@pytest.mark.asyncio
async def test_load_discovery_snapshot_masks_a_credential_before_caching_it():
    """A credential in the accelerator's discovery response is masked before caching."""
    tainted_snapshot = {
        "repos": [{"id": "Proj/RepoA", "name": "RepoA"}],
        "token": _LEAKED_TOKEN,
    }

    async def fake_accel_get(path, session_token=None):
        return tainted_snapshot

    session: dict = {}
    result = await load_discovery_snapshot(session, fake_accel_get)

    assert _LEAKED_TOKEN not in json.dumps(result)
    assert "***" in result.get("token", "")
    assert session["discovery_snapshot"] == result


@pytest.mark.asyncio
async def test_planner_node_masks_a_credential_in_a_freshly_fetched_discovery_snapshot():
    """The planner node's own discovery fetch masks a credential before storing it (CA-003).

    Mirrors the fix already applied to ``nodes/planner_research.py`` in commit
    bf1a07c: this is the planner node's separate inline discovery fetch, which
    bypassed that masking until now.
    """
    tainted_snapshot = {
        "repos": [{"id": "Proj/RepoA", "name": "RepoA"}],
        "token": _LEAKED_TOKEN,
    }
    # Non-empty so `planner_node`'s `state.get("session") or {}` keeps this
    # exact object instead of substituting a throwaway empty dict, which
    # would make the mutations below unobservable to the assertions.
    session: dict = {"session_id": "test-session"}
    state = _make_state(
        session=session,
        accel_get=AsyncMock(return_value=tainted_snapshot),
    )

    await planner_node(state)

    stored = session.get("discovery_snapshot")
    assert stored is not None
    assert _LEAKED_TOKEN not in json.dumps(stored)
    assert "***" in stored.get("token", "")
