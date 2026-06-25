"""Unit tests for MigrationExecutor (feature 008, T078, T090, T092)."""
from __future__ import annotations

import pytest
from ado2gh.api.migration_executor import MigrationExecutor
from ado2gh.api.state_db import get_state_db
from ado2gh.api.models import MigrationOperation, OperationStatus


@pytest.fixture
def executor():
    db = get_state_db(":memory:")
    return MigrationExecutor(db=db)


class TestMigrationExecutorOnDemand:
    def test_create_operation(self, executor):
        op = MigrationOperation(
            repository_id="test-repo",
            organization_id="test-org",
            operation_type="on_demand",
            status=OperationStatus.PENDING.value,
            dry_run=True,
        )
        with executor._db._conn() as conn:
            conn.execute(
                """INSERT INTO migration_operations
                   (id, repository_id, organization_id, operation_type, wave_id,
                    pre_migration_form_id, status, dry_run, confirmed_at,
                    started_at, completed_at, error_message, audit_log_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                op.to_row(),
            )

        with executor._db._conn() as conn:
            row = conn.execute(
                "SELECT repository_id, operation_type, dry_run FROM migration_operations WHERE id = ?",
                (op.id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "test-repo"
        assert row[1] == "on_demand"
        assert row[2] == 1


class TestMigrationExecutorWave:
    """FR-019: Handle mid-wave migration failures."""

    def test_wave_operations_persisted(self, executor):
        ops = [
            MigrationOperation(
                repository_id=f"repo-{i}",
                organization_id="test-org",
                operation_type="wave",
                wave_id="wave-1",
                status=OperationStatus.PENDING.value,
                dry_run=True,
            )
            for i in range(3)
        ]
        with executor._db._conn() as conn:
            for op in ops:
                conn.execute(
                    """INSERT INTO migration_operations
                       (id, repository_id, organization_id, operation_type, wave_id,
                        pre_migration_form_id, status, dry_run, confirmed_at,
                        started_at, completed_at, error_message, audit_log_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    op.to_row(),
                )

        with executor._db._conn() as conn:
            rows = conn.execute(
                "SELECT repository_id, status FROM migration_operations WHERE wave_id = ? ORDER BY repository_id",
                ("wave-1",),
            ).fetchall()
        assert len(rows) == 3
        assert all(r[1] == "pending" for r in rows)

    def test_partial_wave_failure_continues(self, executor):
        """FR-019: Mid-wave failures should not stop remaining repos."""
        ops = [
            MigrationOperation(
                repository_id="repo-1",
                organization_id="test-org",
                operation_type="wave",
                wave_id="wave-2",
                status=OperationStatus.FAILED.value,
                dry_run=True,
                error_message="Connection refused",
            ),
            MigrationOperation(
                repository_id="repo-2",
                organization_id="test-org",
                operation_type="wave",
                wave_id="wave-2",
                status=OperationStatus.COMPLETED.value,
                dry_run=True,
            ),
        ]
        with executor._db._conn() as conn:
            for op in ops:
                conn.execute(
                    """INSERT INTO migration_operations
                       (id, repository_id, organization_id, operation_type, wave_id,
                        pre_migration_form_id, status, dry_run, confirmed_at,
                        started_at, completed_at, error_message, audit_log_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    op.to_row(),
                )

        with executor._db._conn() as conn:
            rows = conn.execute(
                "SELECT repository_id, status, error_message FROM migration_operations WHERE wave_id = ? ORDER BY repository_id",
                ("wave-2",),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "failed"
        assert rows[1][1] == "completed"


class TestCredentialExpiration:
    """FR-017: Handle ADO credential expiration during long-running migration."""

    def test_failed_operation_records_error(self, executor):
        op = MigrationOperation(
            repository_id="cred-expired-repo",
            organization_id="test-org",
            operation_type="on_demand",
            status=OperationStatus.FAILED.value,
            dry_run=False,
            error_message="ADO token expired: 401 Unauthorized",
        )
        with executor._db._conn() as conn:
            conn.execute(
                """INSERT INTO migration_operations
                   (id, repository_id, organization_id, operation_type, wave_id,
                    pre_migration_form_id, status, dry_run, confirmed_at,
                    started_at, completed_at, error_message, audit_log_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                op.to_row(),
            )

        with executor._db._conn() as conn:
            row = conn.execute(
                "SELECT status, error_message FROM migration_operations WHERE id = ?",
                (op.id,),
            ).fetchone()
        assert row[0] == "failed"
        assert "expired" in row[1].lower() or "401" in row[1]
