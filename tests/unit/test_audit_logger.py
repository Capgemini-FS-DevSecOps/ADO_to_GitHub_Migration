"""Unit tests for AuditLogger (feature 008, T079)."""
from __future__ import annotations

import json
import pytest
from ado2gh.api.audit_logger import AuditLogger
from ado2gh.state.db import StateDB


@pytest.fixture
def logger():
    db = StateDB(":memory:")
    return AuditLogger(db=db)


class TestAuditLogger:
    def test_log_event(self, logger):
        logger.log_event(
            operation_id="op-1",
            event_type="migration_started",
            new_state={"dry_run": True},
        )

        with logger._db._conn() as conn:
            row = conn.execute(
                "SELECT event_type, operation_id, new_state_json FROM migration_audit_events ORDER BY timestamp DESC LIMIT 1",
            ).fetchone()
        assert row is not None
        assert row[0] == "migration_started"
        assert row[1] == "op-1"
        details = json.loads(row[2])
        assert details["dry_run"] is True

    def test_log_multiple_events(self, logger):
        for i in range(5):
            logger.log_event(
                operation_id=f"op-{i}",
                event_type=f"event_{i}",
            )

        with logger._db._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM migration_audit_events",
            ).fetchone()[0]
        assert count == 5

    def test_log_with_operation_id(self, logger):
        logger.log_event(
            operation_id="op-abc",
            event_type="migration_completed",
        )

        with logger._db._conn() as conn:
            row = conn.execute(
                "SELECT operation_id FROM migration_audit_events WHERE event_type = ?",
                ("migration_completed",),
            ).fetchone()
        assert row[0] == "op-abc"

    def test_log_with_reason(self, logger):
        logger.log_event(
            operation_id="op-1",
            event_type="rollback",
            previous_state={"status": "completed"},
            new_state={"status": "rolled_back"},
            reason="validation_failed",
        )

        with logger._db._conn() as conn:
            row = conn.execute(
                "SELECT reason FROM migration_audit_events WHERE event_type = ?",
                ("rollback",),
            ).fetchone()
        assert row[0] == "validation_failed"

    def test_log_without_optional_fields(self, logger):
        logger.log_event(
            operation_id="op-1",
            event_type="discovery_scan_started",
        )

        with logger._db._conn() as conn:
            row = conn.execute(
                "SELECT event_type, user_id, reason FROM migration_audit_events WHERE event_type = ?",
                ("discovery_scan_started",),
            ).fetchone()
        assert row is not None
        assert row[0] == "discovery_scan_started"
        assert row[1] == ""
        assert row[2] is None
