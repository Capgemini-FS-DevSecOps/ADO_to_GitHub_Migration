"""Migration execution engine for feature 008.

Handles on-demand and wave-based migration execution with dependency
resolution, dry-run support, and audit trail logging.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from ado2gh.api.audit_logger import AuditLogger
from ado2gh.api.dependency_graph import DependencyGraph, CircularDependencyError
from ado2gh.api.discovery_store import DiscoveryStore
from ado2gh.api.models import (
    MigrationOperation,
    OperationStatus,
    OperationType,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MigrationExecutor:
    """Executes migration operations with dependency resolution and audit logging."""

    def __init__(self, db=None):
        self._db = db
        self._store = DiscoveryStore(db=db) if db else DiscoveryStore()
        self._audit = AuditLogger(db=db) if db else AuditLogger()

    def execute_on_demand(
        self,
        operation_id: str,
        dry_run: bool = False,
    ) -> dict:
        """Execute an on-demand migration operation.

        Returns a summary dict with operation status and migrated repos.
        """
        op = self._get_operation(operation_id)
        if not op:
            raise ValueError(f"Operation {operation_id} not found")

        self._update_status(operation_id, OperationStatus.IN_PROGRESS.value)
        self._audit.log_migrate(
            operation_id=operation_id,
            previous_status=OperationStatus.PENDING.value,
            new_status=OperationStatus.IN_PROGRESS.value,
            dry_run=dry_run,
        )

        try:
            graph = DependencyGraph.from_store(self._store, repository_id=op.repository_id)
            migration_order = graph.get_full_migration_order(op.repository_id)

            if dry_run:
                return {
                    "operation_id": operation_id,
                    "status": "completed",
                    "dry_run": True,
                    "migration_order": migration_order,
                    "dependencies_count": len(migration_order) - 1,
                }

            migrated = self._migrate_repositories(migration_order, op.organization_id)

            self._update_status(operation_id, OperationStatus.COMPLETED.value)
            self._audit.log_migrate(
                operation_id=operation_id,
                previous_status=OperationStatus.IN_PROGRESS.value,
                new_status=OperationStatus.COMPLETED.value,
                migrated_count=len(migrated),
            )

            return {
                "operation_id": operation_id,
                "status": "completed",
                "dependencies_migrated": len(migrated) - 1,
                "migration_order": migration_order,
            }

        except CircularDependencyError as exc:
            self._fail_operation(operation_id, str(exc))
            raise
        except Exception as exc:
            self._fail_operation(operation_id, str(exc))
            raise

    def execute_wave(
        self,
        wave_id: str,
        dry_run: bool = False,
    ) -> dict:
        """Execute a migration wave sequentially in dependency order.

        Continues remaining repos if individual repos fail (FR-019).
        """
        results = {"wave_id": wave_id, "completed": [], "failed": [], "dry_run": dry_run}

        with self._db._conn() as conn:
            repos = conn.execute(
                """SELECT repository_id, organization_id, migration_order
                   FROM wave_repositories WHERE wave_id = ?
                   ORDER BY migration_order ASC""",
                (wave_id,),
            ).fetchall()

        for repo_row in repos:
            repo_id = repo_row[0]
            org_id = repo_row[1]
            try:
                self._migrate_single_repo(repo_id, org_id, dry_run=dry_run)
                results["completed"].append(repo_id)
            except Exception as exc:
                results["failed"].append({"repository_id": repo_id, "error": str(exc)})

        return results

    def _get_operation(self, operation_id: str) -> Optional[MigrationOperation]:
        with self._db._conn() as conn:
            row = conn.execute(
                """SELECT id, repository_id, organization_id, operation_type,
                          wave_id, pre_migration_form_id, status, dry_run,
                          confirmed_at, started_at, completed_at, error_message,
                          audit_log_json
                   FROM migration_operations WHERE id = ?""",
                (operation_id,),
            ).fetchone()
        return MigrationOperation.from_row(row) if row else None

    def _update_status(self, operation_id: str, status: str) -> None:
        with self._db._conn() as conn:
            if status == OperationStatus.IN_PROGRESS.value:
                conn.execute(
                    "UPDATE migration_operations SET status = ?, started_at = ? WHERE id = ?",
                    (status, _now(), operation_id),
                )
            elif status == OperationStatus.COMPLETED.value:
                conn.execute(
                    "UPDATE migration_operations SET status = ?, completed_at = ? WHERE id = ?",
                    (status, _now(), operation_id),
                )
            else:
                conn.execute(
                    "UPDATE migration_operations SET status = ? WHERE id = ?",
                    (status, operation_id),
                )

    def _fail_operation(self, operation_id: str, error: str) -> None:
        with self._db._conn() as conn:
            conn.execute(
                "UPDATE migration_operations SET status = ?, error_message = ?, completed_at = ? WHERE id = ?",
                (OperationStatus.FAILED.value, error, _now(), operation_id),
            )
        self._audit.log_migrate(
            operation_id=operation_id,
            previous_status=OperationStatus.IN_PROGRESS.value,
            new_status=OperationStatus.FAILED.value,
            error=error,
        )

    def _migrate_repositories(self, repo_ids: list[str], org_id: str) -> list[str]:
        """Migrate a list of repositories in order. Returns list of migrated repo IDs."""
        migrated = []
        for repo_id in repo_ids:
            self._migrate_single_repo(repo_id, org_id)
            migrated.append(repo_id)
        return migrated

    def _migrate_single_repo(self, repo_id: str, org_id: str, dry_run: bool = False) -> None:
        """Migrate a single repository. Raises on failure."""
        if dry_run:
            return
        # Placeholder — actual migration delegates to existing Accelerator
        from ado2gh.api.accelerator import Accelerator
        accel = Accelerator()
        accel.migrate_repo(repo_id, org_id)
