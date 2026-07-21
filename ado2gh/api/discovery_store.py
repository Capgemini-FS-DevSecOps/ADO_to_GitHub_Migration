"""Scan result persistence for the unified discovery workflow (feature 008).

Stores DiscoveryResult records and provides query / aggregation methods
used by the discovery API endpoints.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from ado2gh.api.models import DiscoveryResult, DependencyEdge, ScanStatus
from ado2gh.api.state_db import get_state_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiscoveryStore:
    """Persistence layer for scan results and discovery data."""

    def __init__(self, db=None):
        self._db = db or get_state_db()

    @property
    def db(self):
        return self._db

    def save_result(self, result: DiscoveryResult) -> None:
        """Insert or replace a discovery result row."""
        with self._db._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO discovery_results
                   (id, organization_id, repository_id, repository_name,
                    pipeline_count, last_scanned_at, scan_status, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                result.to_row(),
            )

    def save_results_batch(self, results: list[DiscoveryResult]) -> int:
        """Save multiple discovery results. Returns count saved."""
        with self._db._conn() as conn:
            for r in results:
                conn.execute(
                    """INSERT OR REPLACE INTO discovery_results
                       (id, organization_id, repository_id, repository_name,
                        pipeline_count, last_scanned_at, scan_status, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    r.to_row(),
                )
        return len(results)

    def get_results(
        self,
        organization_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[DiscoveryResult]:
        """Retrieve persisted scan results, optionally filtered."""
        query = """SELECT id, organization_id, repository_id, repository_name,
                          pipeline_count, last_scanned_at, scan_status, metadata_json
                   FROM discovery_results"""
        params: list[Any] = []
        clauses: list[str] = []
        if organization_id:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        if status:
            clauses.append("scan_status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY organization_id, repository_name"
        with self._db._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [DiscoveryResult.from_row(row) for row in rows]

    def get_result(self, organization_id: str, repository_id: str) -> Optional[DiscoveryResult]:
        """Retrieve a single discovery result by org + repo."""
        with self._db._conn() as conn:
            row = conn.execute(
                """SELECT id, organization_id, repository_id, repository_name,
                          pipeline_count, last_scanned_at, scan_status, metadata_json
                   FROM discovery_results
                   WHERE organization_id = ? AND repository_id = ?""",
                (organization_id, repository_id),
            ).fetchone()
        return DiscoveryResult.from_row(row) if row else None

    def get_scan_summary(self) -> dict[str, Any]:
        """Return aggregated scan statistics."""
        with self._db._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM discovery_results").fetchone()[0]
            by_status = conn.execute(
                "SELECT scan_status, COUNT(*) FROM discovery_results GROUP BY scan_status"
            ).fetchall()
            by_org = conn.execute(
                "SELECT organization_id, COUNT(*) FROM discovery_results GROUP BY organization_id"
            ).fetchall()
        return {
            "total_repositories": total,
            "by_status": {row[0]: row[1] for row in by_status},
            "by_organization": {row[0]: row[1] for row in by_org},
        }

    def update_scan_status(
        self,
        organization_id: str,
        repository_id: str,
        status: str,
    ) -> None:
        """Update the scan status for a specific repository."""
        with self._db._conn() as conn:
            conn.execute(
                """UPDATE discovery_results
                   SET scan_status = ?, last_scanned_at = ?
                   WHERE organization_id = ? AND repository_id = ?""",
                (status, _now(), organization_id, repository_id),
            )

    def delete_results(self, organization_id: Optional[str] = None) -> int:
        """Delete discovery results, optionally filtered by org. Returns count deleted."""
        with self._db._conn() as conn:
            if organization_id:
                cur = conn.execute(
                    "DELETE FROM discovery_results WHERE organization_id = ?",
                    (organization_id,),
                )
            else:
                cur = conn.execute("DELETE FROM discovery_results")
            return cur.rowcount

    def save_dependency_edge(self, edge: DependencyEdge) -> None:
        """Save a dependency edge to the discovery_dependency_edges table."""
        with self._db._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO discovery_dependency_edges
                   (id, source_repository_id, target_repository_id, dependency_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                edge.to_row(),
            )

    def get_dependency_edges(self, repository_id: Optional[str] = None) -> list[DependencyEdge]:
        """Retrieve dependency edges, optionally filtered by source repo."""
        query = """SELECT id, source_repository_id, target_repository_id, dependency_type, created_at
                   FROM discovery_dependency_edges"""
        params: list[Any] = []
        if repository_id:
            query += " WHERE source_repository_id = ?"
            params.append(repository_id)
        with self._db._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [DependencyEdge.from_row(row) for row in rows]
