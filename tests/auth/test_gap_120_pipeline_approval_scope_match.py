"""Regression check for register entry GAP-120 (GAP-AUTH-13) — an approved pipeline run could start work its approver never saw.

``_assert_migrate_context_matches`` (GAP-071) binds a ``migrate_job`` approval's
stored context to the scope its approver decided on, both at creation and again
at execution. ``_execute_pipeline`` had no equivalent: it forwarded the stored
context straight to the registered executor, which reads ``run_id`` and
``steps`` out of it and starts that run — regardless of what scope id the
approver actually released.

Reproduction: an operator who may operate but may not approve live execution
opens an approval for pipeline run ``run-approved`` carrying
``context={"run_id": "run-different"}``. The approver reads the scope, approves
the run they were shown, and (before this fix) the executor started
``run-different`` instead.

The property asserted here mirrors GAP-071: an approved ``pipeline_run``
executes the run the approver decided on, or it executes nothing. It is
checked twice — at creation, where a context that re-derives to another scope
is refused 422, and again at execution, so a row written before that check
existed still cannot run a different pipeline.

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from ado2gh.api import live_approval_store as store_mod
from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_store import (
    PIPELINE_RUN_CONTEXT_RUN_ID,
    PIPELINE_RUN_SCOPE_TYPE,
    LiveApprovalStore,
    pipeline_run_scope_id,
)
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.state.audit_query import AuditEventFilters
from ado2gh.state.factory import create_state_db

_OPERATOR = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
_APPROVER = PlatformUser("ap-1", "approver1", PlatformRole.APPROVER, "Approver One")

_APPROVED_RUN_ID = "run-approved"
_DIFFERENT_RUN_ID = "run-different"
_APPROVED_SCOPE = pipeline_run_scope_id(_APPROVED_RUN_ID)


@pytest.fixture
def store(tmp_path, monkeypatch) -> LiveApprovalStore:
    """A live-approval store on a throwaway state database."""
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    return LiveApprovalStore(str(db_path))


@pytest.fixture
def executed(monkeypatch) -> list[dict]:
    """Capture what the registered pipeline executor is asked to run."""
    calls: list[dict] = []
    monkeypatch.setattr(store_mod, "_pipeline_executor", calls.append)
    return calls


def _request(scope_id: str, context: dict) -> LiveApprovalCreateRequest:
    """Build the create-approval request an operator would post."""
    return LiveApprovalCreateRequest(
        scope_type=PIPELINE_RUN_SCOPE_TYPE,
        scope_id=scope_id,
        profile_id="prod",
        reason_request="Release pipeline run",
        context=context,
    )


def _audit_events(store: LiveApprovalStore, event_type: str) -> list[dict]:
    """Read the audit events of one type from the store's own database."""
    return create_state_db(store.db_path).search_audit_events(
        AuditEventFilters(event_type=event_type), limit=20,
    )


def _seed_pending(store: LiveApprovalStore, scope_id: str, context: dict) -> str:
    """Write a pending ``pipeline_run`` row straight to the database.

    Bypasses ``create_or_get_pending`` on purpose: this is the row a producer
    that predates the creation check would have left behind, and the point of
    the execution check is that such a row still cannot run.
    """
    approval_id = "lve_gap108seed"
    store.db.create_live_execution_approval(
        approval_id=approval_id,
        requester_user_id=_OPERATOR.id,
        requester_username=_OPERATOR.username,
        scope_type=PIPELINE_RUN_SCOPE_TYPE,
        scope_id=scope_id,
        requested_at=datetime.now(timezone.utc).isoformat(),
        profile_id="prod",
        reason_request="Release pipeline run",
        context_json=json.dumps(context),
    )
    return approval_id


def test_context_naming_another_run_is_refused_at_creation(store, executed):
    """Critical test (a): the approver never sees a scope the context contradicts."""
    with pytest.raises(HTTPException) as exc:
        store.create_or_get_pending(
            _OPERATOR,
            _request(_APPROVED_SCOPE, {PIPELINE_RUN_CONTEXT_RUN_ID: _DIFFERENT_RUN_ID}),
        )

    assert exc.value.status_code == 422, (
        "an approval whose context names a different run was queued; the approver "
        "would decide on run-approved and release run-different"
    )
    assert exc.value.detail["code"] == "scope_context_mismatch"
    assert exc.value.detail["derived_scope_id"] == pipeline_run_scope_id(_DIFFERENT_RUN_ID)
    assert store.list_approvals(status="pending") == []
    assert executed == []


def test_seeded_mismatched_context_is_refused_at_execution_and_audited(store, executed):
    """Critical test (b): the executor re-derives the scope rather than trusting the row."""
    approval_id = _seed_pending(
        store, _APPROVED_SCOPE, {PIPELINE_RUN_CONTEXT_RUN_ID: _DIFFERENT_RUN_ID},
    )

    store.approve(approval_id, _APPROVER, "Release run-approved")

    assert executed == [], (
        "the approved pipeline run executed a context that names another run; the "
        "approver confirmed run-approved (CA-002)"
    )
    mismatches = _audit_events(store, "platform.live_execution.scope_mismatch")
    assert any(approval_id in (e["payload_json"] or "") for e in mismatches), (
        "the refusal left no platform.live_execution.scope_mismatch record (CA-004)"
    )


def test_matching_context_executes_only_the_approved_run_identifier(store, executed):
    """The legitimate pipeline path still runs, forwarding only the run id."""
    row = store.create_or_get_pending(
        _OPERATOR,
        _request(_APPROVED_SCOPE, {
            PIPELINE_RUN_CONTEXT_RUN_ID: _APPROVED_RUN_ID,
            "steps": ["migrate"],
        }),
    )

    store.approve(row["id"], _APPROVER, "Release run-approved")

    assert executed == [{PIPELINE_RUN_CONTEXT_RUN_ID: _APPROVED_RUN_ID}], (
        "unscoped context keys (steps) must not reach the executor"
    )
