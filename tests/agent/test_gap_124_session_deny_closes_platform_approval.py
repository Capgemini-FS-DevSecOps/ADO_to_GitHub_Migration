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

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_scopes import AGENT_SESSION_SCOPE_TYPE
from ado2gh.api.live_approval_store import LiveApprovalStore
from ado2gh.auth.models import PlatformRole, PlatformUser
from services.agent.main import app
from services.agent.routes import execution_routes

client = TestClient(app)

_REQUESTER = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
_APPROVER = PlatformUser("ap-1", "approver1", PlatformRole.APPROVER, "Approver One")


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
