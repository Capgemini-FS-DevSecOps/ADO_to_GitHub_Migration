"""GAP-124 — denying an agent session's live request locally left the platform row open.

``POST /v1/sessions/{id}/approve`` with ``approved: false`` set the session
idle and recorded a local ``session.approve.denied`` audit event, but never
told the platform-side approval row (opened by ``_enqueue_session_live_approval``,
GAP-122) that the request was refused. The row stayed ``pending``, so a
separately-privileged approver could later call
``POST /v1/platform/approvals/{approval_id}/approve`` on that stale row —
``LiveApprovalStore.approve`` does not re-check the session's own denial
state — and resume the session live despite the local denial (CA-002).

Two properties are checked: the route calls the platform's own deny path
(mirroring the approval branch, which already forwards to
``/v1/platform/approvals/{approval_id}/approve``), and — at the level that
actually matters — a store-level deny closes the row so a later ``approve``
call on the same id is refused outright.

A second-review follow-up covers the gap in that first property: the route
audited the local denial only *after* the forward to the platform succeeded,
so a forward that raised — a non-2xx response, or the platform being
unreachable — skipped the audit entirely and left the platform row pending,
while ``resume-live`` flipped a session live unconditionally regardless of
its own denial. The local denial is now audited before the forward is
attempted, a failed forward gets its own audit event, and ``resume-live``
refuses a session that carries a final local denial.

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_scopes import AGENT_SESSION_SCOPE_TYPE
from ado2gh.api.live_approval_store import LiveApprovalStore
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.state.audit_query import AuditEventFilters
from ado2gh.state.factory import create_state_db
from services.agent.main import app
from services.agent.routes import execution_routes

client = TestClient(app)

_REQUESTER = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
_APPROVER = PlatformUser("ap-1", "approver1", PlatformRole.APPROVER, "Approver One")


def _audit_events_for_session(event_type: str, session_id: str) -> list[dict]:
    """Read back audit events of one type, narrowed to one session.

    Conftest points ``ADO2GH_SQLITE_PATH`` at a per-test temp file, so this
    reads the same store the route just wrote to. The session id lives inside
    ``payload_json`` (see ``IdeAuditBridge.record``), not a dedicated column,
    so filtering happens after the JSON decode.
    """
    rows = create_state_db().search_audit_events(
        AuditEventFilters(event_type=event_type), limit=20,
    )
    return [r for r in rows if json.loads(r["payload_json"]).get("session_id") == session_id]


@pytest.mark.asyncio
async def test_local_denial_closes_the_matching_platform_approval_row(monkeypatch):
    """The denial branch forwards to the same deny path the internal route uses."""
    created = client.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]
    execution_routes._sessions[sid]["live_approval_id"] = "lve_gap124"

    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_accel_post(path: str, body: dict, *, session_token: str | None = None) -> dict:
        calls.append((path, body))
        return {"id": "lve_gap124", "status": "denied"}

    monkeypatch.setattr(execution_routes, "_accel_post", AsyncMock(side_effect=_fake_accel_post))

    r = client.post(f"/v1/sessions/{sid}/approve", json={"approved": False, "reason": "not now"})

    assert r.status_code == 200
    assert calls == [("/v1/platform/approvals/lve_gap124/deny", {"reason": "not now"})], (
        "a local denial must close the matching platform approval row through its own "
        "deny path, the same way an approval forwards to the approve path"
    )
    assert execution_routes._sessions[sid]["live_approval_status"] == "denied"


def test_denied_approval_row_cannot_later_be_approved(tmp_path, monkeypatch):
    """The property that matters: once denied, a stale approve call is refused, not honoured."""
    db_path = tmp_path / "gap124_approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    store = LiveApprovalStore(str(db_path))

    row = store.create_or_get_pending(
        _REQUESTER,
        LiveApprovalCreateRequest(
            scope_type=AGENT_SESSION_SCOPE_TYPE,
            scope_id="ses_gap124",
            profile_id="lightweight",
            reason_request="Agent session requested live execution",
            context={},
        ),
    )

    store.deny(row["id"], _APPROVER, "Denied via agent session")

    with pytest.raises(HTTPException) as exc:
        store.approve(row["id"], _APPROVER, "Approved after the fact")

    assert exc.value.status_code == 409, (
        "a denied approval must refuse a later approve call, not resume the session live"
    )


def test_platform_denial_forward_status_failure_still_audits_and_blocks_resume(monkeypatch):
    """A non-2xx response from the platform deny call must not erase the local denial.

    The local denial is audited before the forward is attempted, the failed
    forward gets its own audit event, and a later resume-live call — the
    accelerator's own path back into this session, reachable if the platform
    row were ever approved despite the failed forward — is refused because the
    session still carries a final local denial.
    """
    # The audit bridge caches its writer on first use for the whole process,
    # bound to whatever database was active then; clearing it here forces a
    # fresh writer against *this* test's own temp database (see
    # `_audit_events_for_session`), rather than silently writing into a
    # different test's file.
    execution_routes._audit._writer = None
    created = client.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]
    execution_routes._sessions[sid]["live_approval_id"] = "lve_gap124_status"

    request = httpx.Request("POST", "http://accelerator/v1/platform/approvals/lve_gap124_status/deny")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    monkeypatch.setattr(execution_routes, "_accel_post", AsyncMock(side_effect=status_error))

    r = client.post(f"/v1/sessions/{sid}/approve", json={"approved": False, "reason": "not now"})

    assert r.status_code == 503
    assert execution_routes._sessions[sid]["live_approval_status"] == "denied"

    denied_events = _audit_events_for_session("session.approve.denied", sid)
    assert denied_events, "the local denial must be audited even when the platform forward fails"

    failed_events = _audit_events_for_session("session.approve.denied.forward_failed", sid)
    assert failed_events, "a failed forward must be audited as its own event"

    resume = client.post(f"/v1/internal/sessions/{sid}/resume-live")
    assert resume.status_code == 409, (
        "resume-live must refuse a session with a final local denial, not resume it live"
    )
    assert execution_routes._sessions[sid]["dry_run"] is True


def test_platform_denial_forward_transport_failure_still_audits_and_blocks_resume(monkeypatch):
    """A transport failure (platform unreachable) reaching the deny call is the same story.

    ``httpx.HTTPStatusError`` is not the only way the forward can fail — the
    platform may simply be unreachable, raising a plain ``httpx.RequestError``
    with no response to read a status code from. That must be audited and
    blocked exactly like the status-failure case above.
    """
    execution_routes._audit._writer = None
    created = client.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]
    execution_routes._sessions[sid]["live_approval_id"] = "lve_gap124_transport"

    request = httpx.Request("POST", "http://accelerator/v1/platform/approvals/lve_gap124_transport/deny")
    transport_error = httpx.ConnectError("connection refused", request=request)
    monkeypatch.setattr(execution_routes, "_accel_post", AsyncMock(side_effect=transport_error))

    r = client.post(f"/v1/sessions/{sid}/approve", json={"approved": False, "reason": "not now"})

    assert r.status_code == execution_routes._PLATFORM_UNREACHABLE_STATUS
    assert execution_routes._sessions[sid]["live_approval_status"] == "denied"

    denied_events = _audit_events_for_session("session.approve.denied", sid)
    assert denied_events, "the local denial must be audited even when the platform is unreachable"

    failed_events = _audit_events_for_session("session.approve.denied.forward_failed", sid)
    assert failed_events, "an unreachable platform must be audited as its own forward-failure event"

    resume = client.post(f"/v1/internal/sessions/{sid}/resume-live")
    assert resume.status_code == 409, (
        "resume-live must refuse a session with a final local denial, not resume it live"
    )
    assert execution_routes._sessions[sid]["dry_run"] is True
