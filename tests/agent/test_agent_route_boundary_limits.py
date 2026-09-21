"""Boundary and resource-limit tests for the agent service's HTTP routes.

Every in-memory record the server keeps on a caller's behalf needs both a time limit and a
count limit, or a caller can exhaust server memory simply by making enough requests. Before
this change, ``services/agent/routes/_helpers.py:48-49`` held ``_runs`` and ``_sessions`` as
process-global dicts with no time-to-live and no eviction, removed only by an explicit
``DELETE /v1/sessions/{id}``; the server-sent-event generators in ``message_routes`` and
``form_routes`` sent heartbeats but had no maximum event count. Each retained session carries
a full message list and each open stream holds a graph run, so in the default
auth-disabled configuration an unauthenticated caller could accumulate both (register item
THR-10-001).

No request model declared a single maximum length, and the text goes to the language model
(register item THR-10-002). The health check is authentication-exempt and used to return the
accelerator's address plus the raw connection error, which describes internal network
topology to a caller who was never authenticated (register item THR-02-004). It also awaited
the graph compile step with no timeout, which wedges the whole request — and the test
client's blocking portal with it — whenever that stalls (register item GAP-062).

Eviction is asserted to be lossless-by-design: the sessions dropped here are also held
by ``MigrationSessionStore``, which ``_try_hydrate_session`` reads back.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.agents.migration_agent.constants import (
    MAX_CHAT_MESSAGE_CHARS,
    MAX_FORM_SUBMISSION_CHARS,
)
from services.agent.main import app
from services.agent.routes import _helpers


@pytest.fixture
def agent_client(tmp_path, monkeypatch):
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "bounds.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    yield TestClient(app)
    _helpers._sessions.clear()
    _helpers._runs.clear()


# ─── The in-memory session and run caches are bounded (register item THR-10-001) ───

def _stub_session(updated_at: datetime, run_id: str | None = None) -> dict:
    session = {"session_id": "x", "updated_at": updated_at.isoformat(), "messages": []}
    if run_id:
        session["run_id"] = run_id
    return session


def test_sessions_past_their_ttl_are_evicted(agent_client):
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=_helpers.SESSION_IDLE_TTL_SECONDS + 60,
    )
    _helpers._remember_session("ses_stale", _stub_session(stale))
    _helpers._remember_session("ses_fresh", _stub_session(datetime.now(timezone.utc)))

    _helpers._evict_stale_state()

    assert "ses_stale" not in _helpers._sessions, (
        "a session idle past the TTL was retained; the map has no upper bound"
    )
    assert "ses_fresh" in _helpers._sessions


def test_an_evicted_session_releases_its_run_record(agent_client):
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=_helpers.SESSION_IDLE_TTL_SECONDS + 60,
    )
    _helpers._runs["run_stale"] = {"run_id": "run_stale"}
    _helpers._remember_session("ses_stale", _stub_session(stale, run_id="run_stale"))

    _helpers._evict_stale_state()

    assert "run_stale" not in _helpers._runs


def test_the_session_map_is_capped_by_size(agent_client, monkeypatch):
    monkeypatch.setattr(_helpers, "MAX_IN_MEMORY_SESSIONS", 5)
    base = datetime.now(timezone.utc)

    for i in range(12):
        _helpers._remember_session(
            f"ses_{i:02d}", _stub_session(base + timedelta(seconds=i)),
        )

    assert len(_helpers._sessions) <= 5, (
        f"the session map grew to {len(_helpers._sessions)} past a cap of 5"
    )
    assert "ses_11" in _helpers._sessions, "the newest session was evicted, not the oldest"
    assert "ses_00" not in _helpers._sessions


def test_creating_sessions_over_the_cap_does_not_grow_the_map(agent_client, monkeypatch):
    monkeypatch.setattr(_helpers, "MAX_IN_MEMORY_SESSIONS", 3)

    for _ in range(8):
        created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
        assert created.status_code == 200, created.text

    assert len(_helpers._sessions) <= 3


def test_an_evicted_session_is_rehydrated_from_the_store(agent_client, monkeypatch):
    """Eviction costs a reload, not data — the session still answers."""
    created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
    sid = created.json()["session_id"]
    _helpers._sessions.pop(sid)

    reread = agent_client.get(f"/v1/sessions/{sid}")

    assert reread.status_code == 200, reread.text
    assert reread.json()["session_id"] == sid


# ─── Server-sent event streams are capped (register item THR-10-001) ───────

def test_sse_stream_stops_at_the_event_cap(agent_client, monkeypatch):
    monkeypatch.setattr(
        "ado2gh.agents.migration_agent.constants.SSE_MAX_EVENTS_PER_STREAM", 5,
    )
    created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
    sid = created.json()["session_id"]

    async def _flood(*_args, **_kwargs):
        for i in range(500):
            yield {"kind": "thinking", "content": f"event {i}"}

    monkeypatch.setattr(
        "services.agent.routes.message_routes.stream_user_message", _flood,
    )

    with agent_client.stream(
        "POST", f"/v1/sessions/{sid}/message-stream", json={"message": "go"},
    ) as response:
        frames = [line for line in response.iter_lines() if line.startswith("data: ")]

    assert len(frames) <= 6, (
        f"the stream emitted {len(frames)} frames against a cap of 5; a server-sent event "
        f"run has no upper bound"
    )
    assert json.loads(frames[-1][len("data: "):])["kind"] == "__done__"
    assert _helpers._sessions[sid]["status"] == "idle", (
        "a capped stream left the session busy, so the operator cannot send again"
    )


# ─── Request text is capped at the boundary (register item THR-10-002) ─────

def test_an_oversized_prompt_is_rejected(agent_client):
    created = agent_client.post(
        "/v1/sessions",
        json={"profile_id": "lightweight", "prompt": "a" * (MAX_CHAT_MESSAGE_CHARS + 1)},
    )

    assert created.status_code == 422


def test_an_oversized_chat_message_is_rejected(agent_client):
    created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
    sid = created.json()["session_id"]

    sent = agent_client.post(
        f"/v1/sessions/{sid}/message",
        json={"message": "a" * (MAX_CHAT_MESSAGE_CHARS + 1)},
    )

    assert sent.status_code == 422


def test_a_message_at_the_limit_is_accepted(agent_client):
    created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
    sid = created.json()["session_id"]

    sent = agent_client.post(
        f"/v1/sessions/{sid}/message", json={"message": "a" * MAX_CHAT_MESSAGE_CHARS},
    )

    assert sent.status_code == 200, sent.text


def test_an_oversized_form_submission_is_rejected(agent_client):
    created = agent_client.post("/v1/sessions", json={"profile_id": "lightweight"})
    sid = created.json()["session_id"]

    submitted = agent_client.post(
        f"/v1/sessions/{sid}/form-submit",
        json={"values": {"answer": "a" * (MAX_FORM_SUBMISSION_CHARS + 1)}},
    )

    assert submitted.status_code == 422


# ─── The health check is opaque and bounded (register items THR-02-004, GAP-062) ───

def test_health_does_not_disclose_the_accelerator_url_or_raw_error(agent_client):
    leak = "connect to 10.42.7.9:8080 refused (internal-accel.corp.example)"
    with patch(
        "services.agent.routes.run_routes._check_accelerator", new_callable=AsyncMock,
    ) as probe:
        probe.return_value = (False, leak)
        response = agent_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["accelerator_reachable"] is False
    assert "accelerator_url" not in body
    assert "connection_error" not in body
    assert "10.42.7.9" not in response.text
    assert "internal-accel.corp.example" not in response.text
    assert body["remediation_steps"], "operators still need the remediation guidance"


def test_health_answers_even_when_graph_compilation_never_returns(agent_client, monkeypatch):
    """An unbounded await here wedges the whole request, not just this field (register item GAP-062)."""
    monkeypatch.setattr(
        "services.agent.routes.run_routes.HEALTH_GRAPH_COMPILE_TIMEOUT_SECONDS", 0.1,
    )

    async def _never_returns():
        await asyncio.sleep(3600)

    import ado2gh.agents.migration_agent.graph as graph_module

    monkeypatch.setattr(graph_module, "get_compiled_graph", _never_returns)

    with patch(
        "services.agent.routes.run_routes._check_accelerator", new_callable=AsyncMock,
    ) as probe:
        probe.return_value = (True, None)
        response = agent_client.get("/health")

    assert response.status_code == 200
    assert response.json()["graph_compiled"] is False
