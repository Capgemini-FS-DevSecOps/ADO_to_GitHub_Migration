"""GAP-110 — an agent session's live request opens a real platform approval.

Before this fix, ``_enqueue_session_live_approval`` only flipped
``live_approval_status`` to ``pending`` in memory: it never called the
accelerator, so a live request from the agent chat produced no approval row,
no id an approver could act on, and no audit trail (unlike the equivalent
migrate-job and pipeline-run live requests, which already went through
``POST /v1/platform/approvals``). The fix routes the agent-session request
through that same endpoint.

That endpoint's handler, ``create_live_approval``, also had a latent crash:
``require_operate`` returns ``None`` (not an exception) whenever
``ADO2GH_AUTH_ENABLED`` is unset — the shipped default — and the handler
passed that ``None`` straight into ``LiveApprovalStore.create_or_get_pending``,
whose ``requester`` parameter is not optional and unconditionally reads
``.id``/``.username``/``.role.value``. Every existing caller of that store
method reached it through ``operator_requires_live_approval``, which refuses
an identity-less live request outright — but the new agent-session path calls
the HTTP endpoint directly and does not go through that gate. This file also
covers the guard added to ``create_live_approval`` itself to close that gap
for every caller of the endpoint, present and future.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from services.agent.routes import _helpers
from services.agent.routes._helpers import (
    AGENT_SESSION_SCOPE_TYPE,
    _enqueue_session_live_approval,
)


class _FakeAuditBridge:
    """Captures every ``record`` call instead of writing to a database."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, action: str, **kwargs: Any) -> str:
        self.events.append((action, kwargs))
        return "aud_test"


def _session(session_id: str = "ses_gap100") -> dict[str, Any]:
    return {
        "session_id": session_id,
        "profile_id": "lightweight",
        "session_token": "tok-existing",
        "live_approval_id": None,
        "live_approval_status": None,
        "status": "awaiting_approval",
    }


@pytest.fixture
def fake_audit(monkeypatch):
    bridge = _FakeAuditBridge()
    monkeypatch.setattr(_helpers, "_audit", bridge)
    return bridge


def _mock_accel_client(*, json_body: dict | None = None, status_error: bool = False) -> AsyncMock:
    mock_response = MagicMock()
    if status_error:
        request = httpx.Request("POST", "http://accel/v1/platform/approvals")
        response = httpx.Response(401, request=request)
        mock_response.raise_for_status = MagicMock(side_effect=lambda: response.raise_for_status())
    else:
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = json_body or {}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_live_request_creates_approval_and_is_audited(fake_audit):
    session = _session()
    row = {
        "id": "lve_gap100",
        "scope_type": AGENT_SESSION_SCOPE_TYPE,
        "scope_id": session["session_id"],
        "status": "pending",
    }
    mock_client = _mock_accel_client(json_body=row)

    with patch.object(_helpers.httpx, "AsyncClient", return_value=mock_client) as factory:
        await _enqueue_session_live_approval(session, session_token="tok-request")

    assert session["live_approval_id"] == "lve_gap100"
    assert session["live_approval_status"] == "pending"
    assert session["status"] == "idle"
    posted_path = mock_client.post.call_args.args[0]
    posted_body = mock_client.post.call_args.kwargs["json"]
    assert posted_path == "/v1/platform/approvals"
    assert posted_body["scope_type"] == AGENT_SESSION_SCOPE_TYPE
    assert posted_body["scope_id"] == session["session_id"]
    factory.assert_called_once()
    assert factory.call_args.kwargs["headers"] == {"Cookie": f"{_helpers.SESSION_COOKIE}=tok-request"}

    actions = [action for action, _ in fake_audit.events]
    assert "session.request_live" in actions
    _, kwargs = next(e for e in fake_audit.events if e[0] == "session.request_live")
    assert kwargs["metadata"]["approval_id"] == "lve_gap100"
    assert kwargs["session_id"] == session["session_id"]


@pytest.mark.asyncio
async def test_live_request_failure_is_audited_and_session_still_parks_idle(fake_audit):
    session = _session()
    mock_client = _mock_accel_client(status_error=True)

    with patch.object(_helpers.httpx, "AsyncClient", return_value=mock_client):
        await _enqueue_session_live_approval(session, session_token="tok-request")

    assert session.get("live_approval_id") is None
    assert session["live_approval_status"] == "pending"
    assert session["status"] == "idle"

    actions = [action for action, _ in fake_audit.events]
    assert actions == ["session.request_live.failed"]
    _, kwargs = fake_audit.events[0]
    assert "401" in kwargs["metadata"]["error"]


@pytest.mark.asyncio
async def test_live_request_falls_back_to_the_sessions_own_token(fake_audit):
    session = _session()
    mock_client = _mock_accel_client(json_body={"id": "lve_fallback", "status": "pending"})

    with patch.object(_helpers.httpx, "AsyncClient", return_value=mock_client) as factory:
        await _enqueue_session_live_approval(session)

    assert factory.call_args.kwargs["headers"] == {
        "Cookie": f"{_helpers.SESSION_COOKIE}={session['session_token']}",
    }


@pytest.fixture
def anon_accel_client(tmp_path, monkeypatch):
    """Accelerator reached with no cookie, in the shipped default auth configuration."""
    monkeypatch.delenv("ADO2GH_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "gap100_accel.db"))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api.main import app

    return TestClient(app)


def test_anonymous_caller_cannot_open_a_platform_approval(anon_accel_client):
    """The endpoint the agent now calls must refuse, not crash, with no identity.

    Before the guard in ``create_live_approval``, this request reached
    ``LiveApprovalStore.create_or_get_pending(None, ...)`` and raised an
    unhandled ``AttributeError`` (500) instead of a clean 401 — and the
    default configuration ships with authentication disabled, so this is the
    request every agent session's live-execution request makes by default.
    """
    resp = anon_accel_client.post(
        "/v1/platform/approvals",
        json={
            "scope_type": AGENT_SESSION_SCOPE_TYPE,
            "scope_id": "ses_anonymous",
            "profile_id": "lightweight",
            "reason_request": "Agent session requested live execution",
        },
    )

    assert resp.status_code == 401, (
        f"an identity-less caller must be refused outright; got {resp.status_code} "
        f"body={resp.text}"
    )
