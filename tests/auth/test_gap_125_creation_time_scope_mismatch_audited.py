"""GAP-125 — a creation-time scope-mismatch refusal wrote no audit record.

``_assert_migrate_context_matches`` and ``_assert_pipeline_context_matches``
(GAP-071, GAP-120) already refuse a ``create_or_get_pending`` call whose
context re-derives to a scope other than the one it names, with a 422. The
equivalent refusal at execution time — ``LiveApprovalStore._execute_migrate``
and ``_execute_pipeline`` — writes a ``platform.live_execution.scope_mismatch``
audit event before returning; the creation-time refusal wrote nothing. On the
default ``sqlite`` backend, that left a real refusal — someone tried to open
an approval whose stored context did not match the scope its approver would
be shown — with no trace in the audit trail at all (CA-004).

This mirrors GAP-069/GAP-STATE-06 in shape (a refusal reaches only a log line,
not the audit table) but sits on the default storage path rather than behind
a non-default DynamoDB configuration.

Every identifier below is an obvious fake; no credential value appears (CA-003).
"""
from __future__ import annotations

from fastapi import HTTPException

from ado2gh.api.contracts import LiveApprovalCreateRequest
from ado2gh.api.live_approval_scopes import PIPELINE_RUN_CONTEXT_RUN_ID, PIPELINE_RUN_SCOPE_TYPE
from ado2gh.api.live_approval_store import LiveApprovalStore, migrate_scope_id, pipeline_run_scope_id
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.state.audit_query import AuditEventFilters
from ado2gh.state.factory import create_state_db

_OPERATOR = PlatformUser("op-1", "operator1", PlatformRole.OPERATOR, "Operator One")


def _audit_events(store: LiveApprovalStore, event_type: str) -> list[dict]:
    """Read the audit events of one type from the store's own database."""
    return create_state_db(store.db_path).search_audit_events(
        AuditEventFilters(event_type=event_type), limit=20,
    )


def test_migrate_job_creation_mismatch_is_audited(tmp_path, monkeypatch):
    """A migrate-job context naming another config leaves an audit row, not just a 422."""
    db_path = tmp_path / "gap125_migrate.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    store = LiveApprovalStore(str(db_path))
    approved_scope = migrate_scope_id("prod", 1, "migration.yaml")

    try:
        store.create_or_get_pending(
            _OPERATOR,
            LiveApprovalCreateRequest(
                scope_type="migrate_job",
                scope_id=approved_scope,
                profile_id="prod",
                reason_request="Release wave 1",
                context={"config_path": "OTHER.yaml", "wave_id": 1},
            ),
        )
        raised = False
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 422

    assert raised, "the mismatched context must still be refused (GAP-071)"
    mismatches = _audit_events(store, "platform.live_execution.scope_mismatch")
    assert any(
        row.get("payload_json") and "OTHER.yaml" in row["payload_json"] for row in mismatches
    ), "a creation-time scope mismatch left no platform.live_execution.scope_mismatch record (CA-004)"
    assert any(
        row.get("payload_json") and '"stage": "creation"' in row["payload_json"] for row in mismatches
    ), "the creation-time record should be distinguishable from an execution-time one"


def test_pipeline_run_creation_mismatch_is_audited(tmp_path, monkeypatch):
    """A pipeline-run context naming another run leaves an audit row, not just a 422."""
    db_path = tmp_path / "gap125_pipeline.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    store = LiveApprovalStore(str(db_path))
    approved_scope = pipeline_run_scope_id("run-approved")

    try:
        store.create_or_get_pending(
            _OPERATOR,
            LiveApprovalCreateRequest(
                scope_type=PIPELINE_RUN_SCOPE_TYPE,
                scope_id=approved_scope,
                profile_id="prod",
                reason_request="Release pipeline run",
                context={PIPELINE_RUN_CONTEXT_RUN_ID: "run-different"},
            ),
        )
        raised = False
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 422

    assert raised, "the mismatched context must still be refused (GAP-108)"
    mismatches = _audit_events(store, "platform.live_execution.scope_mismatch")
    assert any(
        row.get("payload_json") and "run-different" in row["payload_json"] for row in mismatches
    ), "a creation-time scope mismatch left no platform.live_execution.scope_mismatch record (CA-004)"
