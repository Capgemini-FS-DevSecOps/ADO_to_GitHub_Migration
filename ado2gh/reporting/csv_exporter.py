"""CSV export utilities for ADO-to-GitHub migration data."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

from ado2gh.logging_config import log
from ado2gh.state.db import StateDB


class CSVExporter:
    """Exports migration data from StateDB as CSV files."""

    # ── Repo migrations ──────────────────────────────────────────────────

    @staticmethod
    def export_migrations(db: StateDB, output_path: str,
                          wave_id: int = None) -> str:
        """Export repo migration status to CSV.

        Args:
            db: StateDB instance to query.
            output_path: Destination CSV file path.
            wave_id: If provided, filter to a single wave; otherwise export all.

        Returns:
            Absolute path of the written file.
        """
        if wave_id is not None:
            rows = db.get_wave_migrations(wave_id)
        else:
            rows = db.get_all_migrations()

        headers = [
            "id", "wave_id", "ado_project", "ado_repo",
            "gh_org", "gh_repo", "scope", "status",
            "started_at", "completed_at", "error_message",
            "gh_migration_id",
        ]

        out = _ensure_path(output_path)
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        log.info("Exported %d repo migrations to %s", len(rows), out)
        return str(out)

    # ── Pipeline migrations ──────────────────────────────────────────────

    @staticmethod
    def export_pipeline_migrations(db: StateDB, output_path: str,
                                   wave_id: int = None) -> str:
        """Export pipeline migration status to CSV.

        Args:
            db: StateDB instance to query.
            output_path: Destination CSV file path.
            wave_id: If provided, filter to a single wave; otherwise export all.

        Returns:
            Absolute path of the written file.
        """
        if wave_id is not None:
            rows = db.get_wave_pipeline_migrations(wave_id)
        else:
            # Gather from all waves by querying all migrations for wave IDs
            all_migrations = db.get_all_migrations()
            wave_ids = sorted({m["wave_id"] for m in all_migrations})
            rows = []
            for wid in wave_ids:
                rows.extend(db.get_wave_pipeline_migrations(wid))

        headers = [
            "id", "wave_id", "project", "pipeline_id", "pipeline_name",
            "repo_name", "gh_org", "gh_repo", "workflow_file",
            "status", "complexity", "started_at", "completed_at",
            "error_message", "warnings_count", "unsupported_count",
        ]

        out = _ensure_path(output_path)
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                enriched = dict(row)
                enriched["warnings_count"] = _count_json(row.get("warnings"))
                enriched["unsupported_count"] = _count_json(row.get("unsupported_tasks"))
                writer.writerow(enriched)

        log.info("Exported %d pipeline migrations to %s", len(rows), out)
        return str(out)

    # ── Risk scores ──────────────────────────────────────────────────────

    @staticmethod
    def export_risk_scores(db: StateDB, output_path: str) -> str:
        """Export risk scores to CSV.

        Args:
            db: StateDB instance to query.
            output_path: Destination CSV file path.

        Returns:
            Absolute path of the written file.
        """
        rows = db.get_all_risk_scores()

        headers = [
            "id", "project", "repo_name", "total_score",
            "assigned_phase", "gh_org", "gh_repo", "scored_at",
        ]

        out = _ensure_path(output_path)
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        log.info("Exported %d risk scores to %s", len(rows), out)
        return str(out)

    # ── Failed repos (retry list) ────────────────────────────────────────

    @staticmethod
    def export_failed_repos(db: StateDB, output_path: str,
                            phase: str = None) -> str:
        """Generate a focused retry list of failed repos.

        Writes a plain-text file (one repo per line) suitable for feeding
        back into the migration tool, plus an accompanying CSV with details.

        Args:
            db: StateDB instance to query.
            output_path: Destination file path (e.g. ``failed_repos.txt``).
            phase: Optional phase filter (matches against ``scope`` field).

        Returns:
            Absolute path of the written file.
        """
        failed = db.get_failed_migrations()

        if phase:
            failed = [r for r in failed if r.get("scope") == phase]

        out = _ensure_path(output_path)

        # Write plain-text retry list
        with open(out, "w", encoding="utf-8") as f:
            for row in failed:
                f.write(f"{row['ado_project']}/{row['ado_repo']}\n")

        # Write accompanying CSV with details
        csv_path = out.with_suffix(".csv")
        headers = [
            "wave_id", "ado_project", "ado_repo", "gh_org", "gh_repo",
            "scope", "status", "error_message",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in failed:
                writer.writerow(row)

        log.info(
            "Exported %d failed repos to %s (details: %s)",
            len(failed), out, csv_path,
        )
        return str(out)


def _ensure_path(path: str) -> Path:
    """Ensure parent directories exist and return a Path object."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _count_json(raw: Optional[str]) -> int:
    """Count elements in a JSON-encoded list string."""
    if not raw:
        return 0
    try:
        data = json.loads(raw)
        return len(data) if isinstance(data, list) else 0
    except (json.JSONDecodeError, TypeError):
        return 0
