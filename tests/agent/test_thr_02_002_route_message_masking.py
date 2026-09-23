"""The agent routes held a second, unmasked writer of the session transcript (register items THR-02-002, GAP-011 residual).

``ado2gh/agents/migration_agent/utils.py:70-99`` documents ``_append_event`` as the
"sole entry point for the in-memory message list, so this is where CA-003 masking is
applied". It was not sole: ``services/agent/routes/_helpers.py:397-417`` ``_add_message``
built and appended its own entry with no ``mask_secrets`` call, and twelve route call
sites across ``session_routes``, ``message_routes``, ``form_routes`` and
``execution_routes`` used it — several with content the client supplies.

The property asserted here is that a secret pasted through *any* route sink is masked
before it reaches the transcript, not that a particular helper was called.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from services.agent.main import _sessions, app
from services.agent.routes._helpers import _add_message

# Obviously-fake literals shaped like the real thing — never a real credential (register item CA-003).
FAKE_GH_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_ADO_PAT = "a7x2k9q4m1p8s3v6y0b5n2h7j4l1d8f3g6t9w2z5c0r7e4u1i8o5"


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "thr02002.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    yield TestClient(app)
    _sessions.clear()


def _new_session(client: TestClient) -> str:
    created = client.post("/v1/sessions", json={"profile_id": "lightweight"})
    assert created.status_code == 200, created.text
    return created.json()["session_id"]


def test_add_message_masks_a_pasted_pat(agent_client):
    sid = _new_session(agent_client)

    _add_message(sid, "user", f"here is my token {FAKE_GH_PAT}", kind="message")

    assert FAKE_GH_PAT not in json.dumps(_sessions[sid]["messages"])


def test_add_message_masks_a_bare_ado_pat(agent_client):
    sid = _new_session(agent_client)

    _add_message(sid, "user", f"ADO_PAT={FAKE_ADO_PAT}", kind="message")

    assert FAKE_ADO_PAT not in json.dumps(_sessions[sid]["messages"])


def test_add_message_keeps_the_bookkeeping_the_routes_rely_on(agent_client):
    """Delegating must not drop ``timestamp``, ``kind``, ``subagent`` or ``updated_at``."""
    sid = _new_session(agent_client)
    before = _sessions[sid]["updated_at"]

    _add_message(sid, "assistant", "Planning the POC wave", kind="thinking", subagent="planner")

    entry = _sessions[sid]["messages"][-1]
    assert entry["role"] == "assistant"
    assert entry["kind"] == "thinking"
    assert entry["subagent"] == "planner"
    assert entry["timestamp"]
    assert _sessions[sid]["updated_at"] >= before


def test_add_message_on_an_unknown_session_is_a_no_op(agent_client):
    _add_message("ses_does_not_exist", "user", "hello", kind="message")

    assert "ses_does_not_exist" not in _sessions


def test_pasted_pat_in_a_chat_message_does_not_reach_the_transcript(agent_client):
    """The route sink an operator actually reaches: POST /v1/sessions/{id}/message."""
    sid = _new_session(agent_client)

    sent = agent_client.post(
        f"/v1/sessions/{sid}/message", json={"message": f"use {FAKE_GH_PAT} to migrate"},
    )

    assert sent.status_code == 200, sent.text
    assert FAKE_GH_PAT not in json.dumps(_sessions[sid]["messages"])
    assert FAKE_GH_PAT not in sent.text
