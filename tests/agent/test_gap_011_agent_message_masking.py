"""GAP-011 (GAP-AGT-02): agent chat, SSE and persisted messages are unmasked.

Reproduction from the gap register's evidence. ``mask_secrets``
(``ado2gh/agents/migration_agent/utils.py:335``) is called from exactly two
places repo-wide -- ``utils.py:384`` and ``utils.py:388``, both inside
``IdeAuditBridge.record``. Every other message sink is unprotected:

* ``_append_event`` / ``_append_and_stream`` (``utils.py``) push content straight
  onto ``session["messages"]`` and out over the LangGraph stream writer that
  backs the SSE chat feed, with no masking call;
* ``_emit_tool_call`` forwards the raw tool ``arguments`` dict verbatim as
  message ``meta``;
* ``MigrationSessionStore.register_http_session`` / ``add_message_to_session``
  ``json.dumps`` the message list into the ``agent_sessions.messages_json``
  SQLite column, so unmasked content reaches a durable, queryable artefact
  rather than just a transient stream.

Blast radius: an operator pastes a PAT into free-text chat (a realistic path --
automated flows use name-only secret mappings). The value is then persisted
verbatim to SQLite and broadcast unmasked over SSE to every viewer of the
session, unconditionally, in the default configuration.

Violates Principle V (CA-003: secret values masked in all messages, logs and
audit records).

These tests assert the security property at each sink -- the raw secret must not
survive into the message list, the SSE event, or the persisted column -- not
that a particular masking helper was called, so they stay valid once T039
consolidates the three redaction implementations onto ``redact_payload``.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from ado2gh.agents.migration_agent.session.store import SCHEMA, MigrationSessionStore
from ado2gh.agents.migration_agent.utils import (
    _append_and_stream,
    _append_event,
    _emit_tool_call,
)

# Obviously-fake literals shaped like the real thing (CA-003 -- never a real credential).
FAKE_GH_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_ADO_PAT = "a7x2k9q4m1p8s3v6y0b5n2h7j4l1d8f3g6t9w2z5c0r7e4u1i8o5"  # 52 chars, opaque


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    class _MockDb:
        def _conn(self):
            return conn

    return MigrationSessionStore(db=_MockDb())


def test_pasted_pat_does_not_reach_session_messages():
    """Operator pastes a PAT into chat; the in-memory message list keeps it."""
    session: dict = {}
    _append_event(
        session,
        role="user",
        content=f"here is my token {FAKE_GH_PAT}, use it to migrate Contoso/api",
    )
    assert FAKE_GH_PAT not in json.dumps(session["messages"])


def test_bare_ado_pat_does_not_reach_session_messages():
    session: dict = {}
    _append_event(session, role="user", content=f"ADO_PAT={FAKE_ADO_PAT}")
    assert FAKE_ADO_PAT not in json.dumps(session["messages"])


def test_tool_call_arguments_do_not_carry_a_raw_secret():
    """_emit_tool_call forwards the tool `arguments` dict into message meta."""
    session: dict = {}
    _emit_tool_call(
        session,
        "github_api",
        subagent="executor",
        arguments={"token": FAKE_GH_PAT, "repo": "Contoso/api"},
    )
    blob = json.dumps(session["messages"])
    assert FAKE_GH_PAT not in blob
    assert "Contoso/api" in blob  # non-secret context is preserved


def test_sse_stream_event_does_not_carry_a_raw_secret(monkeypatch):
    """_append_and_stream is the SSE broadcast path for the chat feed."""
    import langgraph.config

    emitted: list[dict] = []
    monkeypatch.setattr(langgraph.config, "get_stream_writer", lambda: emitted.append)

    session: dict = {}
    _append_and_stream(
        session,
        role="assistant",
        content=f"Authenticating with {FAKE_GH_PAT}…",
        kind="status",
        subagent="executor",
    )

    assert emitted, "stream writer was not invoked - SSE path did not run"
    assert FAKE_GH_PAT not in json.dumps(emitted)


def test_persisted_session_messages_column_has_no_raw_secret(store):
    """add_message_to_session writes into agent_sessions.messages_json."""
    sid = store.create_session(profile_id="lightweight")["session_id"]
    store.add_message_to_session(sid, "user", f"my pat is {FAKE_GH_PAT}", kind="text")

    with store._db._conn() as conn:
        raw = conn.execute(
            "SELECT messages_json FROM agent_sessions WHERE session_id=?", (sid,)
        ).fetchone()["messages_json"]
    assert FAKE_GH_PAT not in raw


def test_registered_http_session_messages_column_has_no_raw_secret(store):
    """register_http_session json.dumps the whole message list into the column."""
    session = {
        "session_id": "ses_gap011",
        "profile_id": "lightweight",
        "messages": [
            {"role": "user", "content": f"token: {FAKE_GH_PAT}", "kind": "text"},
            {"role": "user", "content": f"ADO PAT {FAKE_ADO_PAT}", "kind": "text"},
        ],
    }
    store.register_http_session(session)

    with store._db._conn() as conn:
        raw = conn.execute(
            "SELECT messages_json FROM agent_sessions WHERE session_id=?",
            ("ses_gap011",),
        ).fetchone()["messages_json"]
    assert FAKE_GH_PAT not in raw
    assert FAKE_ADO_PAT not in raw
