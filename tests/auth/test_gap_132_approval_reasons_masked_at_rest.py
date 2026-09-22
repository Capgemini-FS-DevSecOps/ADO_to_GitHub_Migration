"""Regression check for register entry GAP-132 — approval reasons were persisted and returned unmasked.

``LiveApprovalStore.create_or_get_pending`` stored ``reason_request`` exactly as
the caller supplied it, and ``approve``/``deny`` stored ``reason_decision`` the
same way, while ``_public_row`` handed both straight back out. The audit event
written alongside each of those calls was already masked through
``redact_payload`` — only the approval row itself, and the API response built
from it, carried the raw value. The queue never expires a row, so a secret
typed into either reason field outlives the run it released (CA-003).

The forward to the agent service on a denial (``_notify_agent`` with the
``deny-live`` route) carries the same reason and has to see the masked text
too, not the raw one.

Every token below is an obvious fake; it is a shape, not a credential (CA-003).
"""
from __future__ import annotations

from typing import Any

import pytest

from ado2gh.api import live_approval_store as store_mod
from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_scopes import AGENT_SESSION_SCOPE_TYPE
from ado2gh.api.live_approval_store import LiveApprovalStore
from ado2gh.auth.models import PlatformRole, PlatformUser

_OPERATOR = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
_APPROVER = PlatformUser("ap-1", "approver1", PlatformRole.APPROVER, "Approver One")

# Shape `redact_text` recognises. Not a credential; 20 fake characters chosen
# to match the GitHub token pattern.
_FAKE_GH_TOKEN = "ghp_" + "0123456789abcdefghij"


@pytest.fixture
def store(tmp_path, monkeypatch) -> LiveApprovalStore:
    """A live-approval store on a throwaway state database."""
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    return LiveApprovalStore(str(db_path))


def _raw_row(store: LiveApprovalStore, approval_id: str) -> dict:
    """Read the approval row exactly as it sits in the database, unmasked."""
    return store.db.get_live_execution_approval(approval_id)


def test_a_token_in_the_request_reason_is_masked_before_it_is_persisted(store):
    """Critical property: no recognised secret shape reaches the reason_request column."""
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type=AGENT_SESSION_SCOPE_TYPE,
            scope_id="sess_gap132_req",
            reason_request=f"Ready for live, retry with {_FAKE_GH_TOKEN}",
            context={},
        ),
    )

    raw = _raw_row(store, row["id"])
    assert _FAKE_GH_TOKEN not in raw["reason_request"]
    assert "***" in raw["reason_request"]
    assert row["reason_request"] == raw["reason_request"]


def test_a_token_in_the_decision_reason_is_masked_before_it_is_persisted(store):
    """The same property holds for `reason_decision`, written by `approve`."""
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type=AGENT_SESSION_SCOPE_TYPE,
            scope_id="sess_gap132_dec",
            context={},
        ),
    )

    decided = store.approve(row["id"], _APPROVER, f"Approved, token is {_FAKE_GH_TOKEN}")

    raw = _raw_row(store, row["id"])
    assert _FAKE_GH_TOKEN not in raw["reason_decision"]
    assert "***" in raw["reason_decision"]
    assert decided["reason_decision"] == raw["reason_decision"]


def test_a_preexisting_unmasked_row_is_masked_on_read(store):
    """A row written before this fix existed must still not leak through the API."""
    row = store.db.create_live_execution_approval(
        approval_id="lve_gap132_legacy",
        requester_user_id=_OPERATOR.id,
        requester_username=_OPERATOR.username,
        scope_type=AGENT_SESSION_SCOPE_TYPE,
        scope_id="sess_gap132_legacy",
        requested_at="2025-01-01T00:00:00+00:00",
        reason_request=f"legacy reason with {_FAKE_GH_TOKEN}",
    )
    assert _FAKE_GH_TOKEN in row["reason_request"], "sanity: the raw column really is unmasked"

    public = store.get_approval("lve_gap132_legacy")

    assert _FAKE_GH_TOKEN not in public["reason_request"]
    assert "***" in public["reason_request"]


def test_deny_forwards_the_masked_reason_to_the_agent_service(store, monkeypatch):
    """The notify path to the agent (`deny-live`) must not carry the raw reason."""
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type=AGENT_SESSION_SCOPE_TYPE,
            scope_id="sess_gap132_deny",
            context={},
        ),
    )

    sent: list[dict[str, Any]] = []

    class _FakeResponse:
        is_success = True
        status_code = 200

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002 - matches httpx.post's kwarg name
        sent.append(json)
        return _FakeResponse()

    monkeypatch.setattr(store_mod.httpx, "post", _fake_post)

    store.deny(row["id"], _APPROVER, f"Denied, saw {_FAKE_GH_TOKEN} in the request")

    assert len(sent) == 1
    assert _FAKE_GH_TOKEN not in sent[0]["reason"]
    assert "***" in sent[0]["reason"]
