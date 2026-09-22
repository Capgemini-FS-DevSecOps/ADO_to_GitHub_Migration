"""Regression check for register entry GAP-071 (GAP-AUTH-09) — an approved migrate job ran work its approver never saw.

``POST /v1/platform/approvals`` takes a free-form ``context`` dict from the caller.
``_public_row`` omits it, so the approver's queue shows a ``scope_id`` and nothing
else. On approval, ``_execute_migrate`` handed that same context straight to
``_execute_approved_migrate``, which builds ``RunWaveRequest(**ctx)`` and runs it.

Reproduction: an operator who may operate but may not approve live execution opens
an approval for ``migrate:prod:1:migration.yaml`` carrying
``context={"config_path": "OTHER.yaml"}``. The approver reads the scope, approves
wave 1 of ``migration.yaml``, and the executor runs every wave of ``OTHER.yaml``
live. The confirmation CA-002 requires was given for different work than the work
that ran.

The property asserted here is one sentence: an approved ``migrate_job`` executes
the scope the approver decided on, or it executes nothing. It is checked twice —
at creation, where a context that re-derives to another scope is refused 422, and
again at execution, so a row written before that check existed still cannot run.

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from ado2gh.api import live_approval_store as store_mod
from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_store import LiveApprovalStore, migrate_scope_id
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.state.audit_query import AuditEventFilters
from ado2gh.state.factory import create_state_db

_OPERATOR = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")
_APPROVER = PlatformUser("ap-1", "approver1", PlatformRole.APPROVER, "Approver One")

_APPROVED_SCOPE = migrate_scope_id("prod", 1, "migration.yaml")


@pytest.fixture
def store(tmp_path, monkeypatch) -> LiveApprovalStore:
    """A live-approval store on a throwaway state database."""
    db_path = tmp_path / "approvals.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    return LiveApprovalStore(str(db_path))


@pytest.fixture
def executed(monkeypatch) -> list[dict]:
    """Capture what the registered migrate executor is asked to run."""
    calls: list[dict] = []
    monkeypatch.setattr(store_mod, "_migrate_executor", calls.append)
    return calls


def _request(scope_id: str, context: dict) -> LiveApprovalCreateRequest:
    """Build the create-approval request an operator would post."""
    return LiveApprovalCreateRequest(
        scope_type="migrate_job",
        scope_id=scope_id,
        profile_id="prod",
        reason_request="Release wave 1",
        context=context,
    )


def _audit_events(store: LiveApprovalStore, event_type: str) -> list[dict]:
    """Read the audit events of one type from the store's own database."""
    return create_state_db(store.db_path).search_audit_events(
        AuditEventFilters(event_type=event_type), limit=20,
    )


def _seed_pending(store: LiveApprovalStore, scope_id: str, context_json: str) -> str:
    """Write a pending ``migrate_job`` row straight to the database.

    Bypasses ``create_or_get_pending`` on purpose: this is the row a producer
    that predates the creation check would have left behind, and the point of
    the execution check is that such a row still cannot run.
    """
    approval_id = "lve_gap071seed"
    store.db.create_live_execution_approval(
        approval_id=approval_id,
        requester_user_id=_OPERATOR.id,
        requester_username=_OPERATOR.username,
        scope_type="migrate_job",
        scope_id=scope_id,
        requested_at=datetime.now(timezone.utc).isoformat(),
        profile_id="prod",
        reason_request="Release wave 1",
        context_json=context_json,
    )
    return approval_id


def test_context_naming_another_config_is_refused_at_creation(store, executed):
    """Critical test (a): the approver never sees a scope the context contradicts."""
    with pytest.raises(HTTPException) as exc:
        store.create_or_get_pending(
            _OPERATOR, _request(_APPROVED_SCOPE, {"config_path": "OTHER.yaml", "wave_id": 1}),
        )

    assert exc.value.status_code == 422, (
        "an approval whose context names a different config was queued; the approver "
        "would decide on migration.yaml and release OTHER.yaml"
    )
    assert exc.value.detail["code"] == "scope_context_mismatch"
    assert store.list_approvals(status="pending") == []
    assert executed == []


def test_context_dropping_the_wave_is_refused_at_creation(store):
    """A context that omits ``wave_id`` asks for every wave, not the one on the scope."""
    with pytest.raises(HTTPException) as exc:
        store.create_or_get_pending(
            _OPERATOR, _request(_APPROVED_SCOPE, {"config_path": "migration.yaml"}),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["derived_scope_id"] == migrate_scope_id(
        "prod", None, "migration.yaml",
    )


def test_dry_run_context_on_a_live_approval_is_refused(store):
    """A queued approval releases a live run; a dry-run context contradicts itself."""
    with pytest.raises(HTTPException) as exc:
        store.create_or_get_pending(
            _OPERATOR,
            _request(_APPROVED_SCOPE, {
                "config_path": "migration.yaml", "wave_id": 1, "dry_run": True,
            }),
        )

    assert exc.value.status_code == 422


def test_seeded_mismatched_context_is_refused_at_execution_and_audited(store, executed):
    """Critical test (b): the executor re-derives the scope rather than trusting the row."""
    approval_id = _seed_pending(
        store, _APPROVED_SCOPE, '{"config_path": "OTHER.yaml", "dry_run": false}',
    )

    store.approve(approval_id, _APPROVER, "Release wave 1 of migration.yaml")

    assert executed == [], (
        "the approved migrate job executed a context that names another config; the "
        "approver confirmed migration.yaml wave 1 (CA-002)"
    )
    mismatches = _audit_events(store, "platform.live_execution.scope_mismatch")
    assert any(approval_id in (e["payload_json"] or "") for e in mismatches), (
        "the refusal left no platform.live_execution.scope_mismatch record (CA-004)"
    )


def test_matching_context_executes_the_approved_scope_live(store, executed):
    """The legitimate dashboard path still runs, and runs live."""
    row = store.create_or_get_pending(
        _OPERATOR,
        _request(_APPROVED_SCOPE, {
            "config_path": "migration.yaml",
            "wave_id": 1,
            "dry_run": False,
            "db_path": "migration_state.db",
        }),
    )

    store.approve(row["id"], _APPROVER, "Release wave 1")

    assert executed == [{
        "config_path": "migration.yaml", "wave_id": 1,
        "db_path": "migration_state.db", "dry_run": False,
    }]


def test_unscoped_context_keys_never_reach_the_executor(store, executed):
    """Only what the scope encodes is rebuilt; the rest of the context is dropped."""
    row = store.create_or_get_pending(
        _OPERATOR,
        _request(_APPROVED_SCOPE, {
            "config_path": "migration.yaml", "wave_id": 1,
            "live_approval_id": "lve_someoneelses", "profile_id": "staging",
        }),
    )

    store.approve(row["id"], _APPROVER, "Release wave 1")

    assert executed and set(executed[0]) == {
        "config_path", "wave_id", "db_path", "dry_run",
    }


@pytest.mark.parametrize("wave", [" 1", "01", 1.0, True])
def test_scope_spelling_cannot_differ_from_the_executed_wave(store, executed, wave):
    """The scope shown and the wave run are one reading of one value.

    ``RunWaveRequest.wave_id`` is ``Optional[int]``, so pydantic turns all four of
    these into wave 1 while each renders a different scope id. Deriving the scope
    from the raw context let the approver be shown a spelling the executor never
    saw; deriving it from the validated request closes the class.
    """
    crafted = f"migrate:prod:{wave}:migration.yaml"
    with pytest.raises(HTTPException) as exc:
        store.create_or_get_pending(
            _OPERATOR, _request(crafted, {"config_path": "migration.yaml", "wave_id": wave}),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["derived_scope_id"] == _APPROVED_SCOPE
    assert executed == []


def test_feature_route_context_still_grants_without_executing(store, executed):
    """``migrate_guard``'s context has no wave to replay — it must still be decidable."""
    scope_id = "/v1/migrate/git-mirror:Contoso:payments"
    row = store.create_or_get_pending(
        _OPERATOR,
        LiveApprovalCreateRequest(
            scope_type="migrate_job",
            scope_id=scope_id,
            profile_id="prod",
            reason_request="Live /v1/migrate/git-mirror",
            context={"route": "/v1/migrate/git-mirror", "scope_id": scope_id},
        ),
    )

    decided = store.approve(row["id"], _APPROVER, "Release the feature route")

    assert decided["status"] == "approved"
    assert executed == []
    assert store.has_approved("migrate_job", scope_id) is True
