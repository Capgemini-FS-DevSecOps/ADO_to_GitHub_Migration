"""SQLite state persistence — migration tracking, pipeline inventory, risk scores, gates."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ado2gh.models import (
    BatchCheckpoint, MigrationStatus, PhaseGateResult, PhaseType,
    PipelineMetadata, RepoConfig,
)


class StateDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS migrations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id         INTEGER NOT NULL,
        ado_project     TEXT NOT NULL,
        ado_repo        TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        scope           TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        started_at      TEXT,
        completed_at    TEXT,
        error_message   TEXT,
        gh_migration_id TEXT,
        stats           TEXT,
        UNIQUE(wave_id, ado_project, ado_repo, scope)
    );

    CREATE TABLE IF NOT EXISTS wave_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id      INTEGER NOT NULL,
        started_at   TEXT,
        completed_at TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        dry_run      INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS pipeline_inventory (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        pipeline_id     INTEGER NOT NULL,
        pipeline_name   TEXT NOT NULL,
        pipeline_type   TEXT NOT NULL DEFAULT 'yaml',
        repo_id         TEXT NOT NULL DEFAULT '',
        repo_name       TEXT NOT NULL DEFAULT '',
        folder          TEXT NOT NULL DEFAULT '',
        complexity      TEXT NOT NULL DEFAULT 'simple',
        metadata_json   TEXT NOT NULL DEFAULT '{}',
        scanned_at      TEXT,
        UNIQUE(project, pipeline_id)
    );

    CREATE TABLE IF NOT EXISTS pipeline_migrations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id         INTEGER NOT NULL,
        project         TEXT NOT NULL,
        pipeline_id     INTEGER NOT NULL,
        pipeline_name   TEXT NOT NULL,
        repo_name       TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        workflow_file   TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        started_at      TEXT,
        completed_at    TEXT,
        error_message   TEXT,
        warnings        TEXT,
        unsupported_tasks TEXT,
        complexity      TEXT,
        transform_stats TEXT,
        UNIQUE(wave_id, project, pipeline_id)
    );

    CREATE TABLE IF NOT EXISTS repo_risk_scores (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        project        TEXT NOT NULL,
        repo_name      TEXT NOT NULL,
        total_score    REAL NOT NULL DEFAULT 0,
        assigned_phase TEXT,
        gh_org         TEXT NOT NULL DEFAULT '',
        gh_repo        TEXT NOT NULL DEFAULT '',
        score_json     TEXT NOT NULL DEFAULT '{}',
        scored_at      TEXT,
        UNIQUE(project, repo_name)
    );

    CREATE TABLE IF NOT EXISTS phase_gates (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        phase                TEXT NOT NULL,
        status               TEXT,
        repo_success_pct     REAL,
        pipeline_success_pct REAL,
        repos_completed      INTEGER,
        repos_total          INTEGER,
        pipelines_completed  INTEGER,
        pipelines_total      INTEGER,
        failures_json        TEXT,
        override_reason      TEXT,
        checked_at           TEXT,
        UNIQUE(phase)
    );

    CREATE TABLE IF NOT EXISTS batch_checkpoints (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        phase         TEXT NOT NULL,
        batch_num     INTEGER NOT NULL,
        total_batches INTEGER NOT NULL,
        repos_done    INTEGER NOT NULL DEFAULT 0,
        repos_total   INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'pending',
        started_at    TEXT,
        completed_at  TEXT,
        UNIQUE(phase, batch_num)
    );
    """

    def __init__(self, db_path: str = "migration_state.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ── Repo-scope migrations ───────────────────────────────────────────────

    def upsert_migration(self, wave_id: int, repo: RepoConfig, scope: str,
                         status: MigrationStatus, error: str = None,
                         gh_migration_id: str = None, stats: dict = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO migrations
                    (wave_id, ado_project, ado_repo, gh_org, gh_repo, scope,
                     status, started_at, completed_at, error_message,
                     gh_migration_id, stats)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wave_id, ado_project, ado_repo, scope)
                DO UPDATE SET
                    status          = excluded.status,
                    started_at      = COALESCE(migrations.started_at, excluded.started_at),
                    completed_at    = excluded.completed_at,
                    error_message   = excluded.error_message,
                    gh_migration_id = excluded.gh_migration_id,
                    stats           = excluded.stats
            """, (
                wave_id, repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
                scope, status.value,
                now if status == MigrationStatus.IN_PROGRESS else None,
                now if status in (MigrationStatus.COMPLETED, MigrationStatus.FAILED,
                                  MigrationStatus.ROLLED_BACK) else None,
                error, gh_migration_id,
                json.dumps(stats) if stats else None,
            ))

    def get_wave_migrations(self, wave_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM migrations WHERE wave_id=? ORDER BY id", (wave_id,)
            ).fetchall()]

    def get_all_migrations(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM migrations ORDER BY wave_id, id"
            ).fetchall()]

    def get_failed_migrations(self, wave_id: int = None) -> list[dict]:
        with self._conn() as conn:
            if wave_id:
                return [dict(r) for r in conn.execute(
                    "SELECT * FROM migrations WHERE wave_id=? AND status='failed'",
                    (wave_id,)
                ).fetchall()]
            return [dict(r) for r in conn.execute(
                "SELECT * FROM migrations WHERE status='failed'"
            ).fetchall()]

    def wave_summary(self, wave_id: int) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT scope, status, COUNT(*) as cnt FROM migrations "
                "WHERE wave_id=? GROUP BY scope, status", (wave_id,)
            ).fetchall()
        result: dict = {}
        for r in rows:
            result.setdefault(r["scope"], {})[r["status"]] = r["cnt"]
        return result

    def mark_wave_run(self, wave_id: int, status: str, dry_run: bool = False) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            if status == "started":
                cur = conn.execute(
                    "INSERT INTO wave_runs (wave_id, started_at, status, dry_run) "
                    "VALUES (?,?,?,?)", (wave_id, now, "in_progress", int(dry_run))
                )
                return cur.lastrowid
            else:
                conn.execute(
                    "UPDATE wave_runs SET completed_at=?, status=? "
                    "WHERE wave_id=? AND completed_at IS NULL",
                    (now, status, wave_id)
                )
                return -1

    # ── Pipeline inventory ──────────────────────────────────────────────────

    def upsert_pipeline_inventory(self, meta: PipelineMetadata):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO pipeline_inventory
                    (project, pipeline_id, pipeline_name, pipeline_type,
                     repo_id, repo_name, folder, complexity, metadata_json, scanned_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project, pipeline_id)
                DO UPDATE SET
                    pipeline_name = excluded.pipeline_name,
                    pipeline_type = excluded.pipeline_type,
                    repo_id       = excluded.repo_id,
                    repo_name     = excluded.repo_name,
                    folder        = excluded.folder,
                    complexity    = excluded.complexity,
                    metadata_json = excluded.metadata_json,
                    scanned_at    = excluded.scanned_at
            """, (
                meta.project, meta.pipeline_id, meta.pipeline_name,
                meta.pipeline_type.value, meta.repo_id, meta.repo_name,
                meta.folder, meta.complexity.value,
                json.dumps(meta.to_dict()), now,
            ))

    def get_pipelines_for_repo(self, project: str, repo_name: str) -> list[PipelineMetadata]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT metadata_json FROM pipeline_inventory "
                "WHERE project=? AND repo_name=? ORDER BY pipeline_id",
                (project, repo_name)
            ).fetchall()
        return [PipelineMetadata.from_dict(json.loads(r["metadata_json"])) for r in rows]

    def get_all_inventory(self, project: str = None) -> list[dict]:
        with self._conn() as conn:
            if project:
                rows = conn.execute(
                    "SELECT * FROM pipeline_inventory WHERE project=? ORDER BY pipeline_id",
                    (project,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pipeline_inventory ORDER BY project, pipeline_id"
                ).fetchall()
        return [dict(r) for r in rows]

    def inventory_count(self, project: str = None) -> int:
        with self._conn() as conn:
            if project:
                return conn.execute(
                    "SELECT COUNT(*) FROM pipeline_inventory WHERE project=?", (project,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM pipeline_inventory").fetchone()[0]

    def inventory_count_for_repo(self, project: str, repo_name: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM pipeline_inventory WHERE project=? AND repo_name=?",
                (project, repo_name)
            ).fetchone()[0]

    def clear_inventory(self, project: str = None):
        with self._conn() as conn:
            if project:
                conn.execute("DELETE FROM pipeline_inventory WHERE project=?", (project,))
            else:
                conn.execute("DELETE FROM pipeline_inventory")

    # ── Pipeline migrations ─────────────────────────────────────────────────

    def upsert_pipeline_migration(self, wave_id: int, meta: PipelineMetadata,
                                  gh_org: str, gh_repo: str,
                                  status: MigrationStatus,
                                  workflow_file: str = None,
                                  error: str = None,
                                  warnings: list = None,
                                  unsupported: list = None,
                                  transform_stats: dict = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO pipeline_migrations
                    (wave_id, project, pipeline_id, pipeline_name, repo_name,
                     gh_org, gh_repo, workflow_file, status,
                     started_at, completed_at, error_message,
                     warnings, unsupported_tasks, complexity, transform_stats)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wave_id, project, pipeline_id)
                DO UPDATE SET
                    status            = excluded.status,
                    workflow_file     = excluded.workflow_file,
                    started_at        = COALESCE(pipeline_migrations.started_at,
                                                 excluded.started_at),
                    completed_at      = excluded.completed_at,
                    error_message     = excluded.error_message,
                    warnings          = excluded.warnings,
                    unsupported_tasks = excluded.unsupported_tasks,
                    transform_stats   = excluded.transform_stats
            """, (
                wave_id, meta.project, meta.pipeline_id, meta.pipeline_name,
                meta.repo_name, gh_org, gh_repo, workflow_file,
                status.value,
                now if status == MigrationStatus.IN_PROGRESS else None,
                now if status in (MigrationStatus.COMPLETED, MigrationStatus.FAILED) else None,
                error,
                json.dumps(warnings or []),
                json.dumps(unsupported or []),
                meta.complexity.value,
                json.dumps(transform_stats or {}),
            ))

    def get_wave_pipeline_migrations(self, wave_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM pipeline_migrations WHERE wave_id=? ORDER BY id",
                (wave_id,)
            ).fetchall()]

    def get_failed_pipeline_migrations(self, wave_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM pipeline_migrations "
                "WHERE wave_id=? AND status IN ('failed','pending') ORDER BY id",
                (wave_id,)
            ).fetchall()]

    def pipeline_migration_summary(self, wave_id: int) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, complexity, COUNT(*) as cnt "
                "FROM pipeline_migrations WHERE wave_id=? "
                "GROUP BY status, complexity", (wave_id,)
            ).fetchall()
        result: dict = {"by_status": {}, "by_complexity": {}}
        for r in rows:
            result["by_status"][r["status"]] = \
                result["by_status"].get(r["status"], 0) + r["cnt"]
            result["by_complexity"][r["complexity"]] = \
                result["by_complexity"].get(r["complexity"], 0) + r["cnt"]
        return result

    def reset_failed_pipeline_migrations(self, wave_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM pipeline_migrations WHERE wave_id=? AND status='failed'",
                (wave_id,)
            )

    # ── Risk scores ─────────────────────────────────────────────────────────

    def upsert_risk_score(self, score):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO repo_risk_scores
                    (project,repo_name,total_score,assigned_phase,gh_org,gh_repo,score_json,scored_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(project,repo_name) DO UPDATE SET
                    total_score=excluded.total_score,
                    assigned_phase=excluded.assigned_phase,
                    gh_org=excluded.gh_org, gh_repo=excluded.gh_repo,
                    score_json=excluded.score_json, scored_at=excluded.scored_at
            """, (
                score.project, score.repo_name, score.total_score,
                score.assigned_phase.value if score.assigned_phase else None,
                score.gh_org, score.gh_repo,
                json.dumps(score.to_dict()), now,
            ))

    def get_all_risk_scores(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM repo_risk_scores ORDER BY total_score"
            ).fetchall()]

    def get_risk_scores_for_phase(self, phase) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM repo_risk_scores WHERE assigned_phase=? ORDER BY total_score",
                (phase.value,)
            ).fetchall()]

    def risk_score_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM repo_risk_scores").fetchone()[0]

    # ── Phase gates ─────────────────────────────────────────────────────────

    def upsert_phase_gate(self, result):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO phase_gates
                    (phase,status,repo_success_pct,pipeline_success_pct,
                     repos_completed,repos_total,pipelines_completed,pipelines_total,
                     failures_json,override_reason,checked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(phase) DO UPDATE SET
                    status=excluded.status,
                    repo_success_pct=excluded.repo_success_pct,
                    pipeline_success_pct=excluded.pipeline_success_pct,
                    repos_completed=excluded.repos_completed,
                    repos_total=excluded.repos_total,
                    pipelines_completed=excluded.pipelines_completed,
                    pipelines_total=excluded.pipelines_total,
                    failures_json=excluded.failures_json,
                    override_reason=excluded.override_reason,
                    checked_at=excluded.checked_at
            """, (
                result.phase.value, result.status.value,
                result.repo_success_pct, result.pipeline_success_pct,
                result.repos_completed, result.repos_total,
                result.pipelines_completed, result.pipelines_total,
                json.dumps(result.failures), result.override_reason,
                result.checked_at or now,
            ))

    def get_phase_gate(self, phase) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM phase_gates WHERE phase=?",
                               (phase.value,)).fetchone()
            return dict(row) if row else None

    def get_all_phase_gates(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM phase_gates ORDER BY rowid"
            ).fetchall()]

    # ── Batch checkpoints ───────────────────────────────────────────────────

    def upsert_batch_checkpoint(self, cp):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO batch_checkpoints
                    (phase,batch_num,total_batches,repos_done,repos_total,status,started_at,completed_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(phase,batch_num) DO UPDATE SET
                    repos_done=excluded.repos_done, status=excluded.status,
                    completed_at=excluded.completed_at
            """, (
                cp.phase.value, cp.batch_num, cp.total_batches,
                cp.repos_done, cp.repos_total, cp.status,
                cp.started_at or now, cp.completed_at,
            ))

    def get_batch_checkpoints(self, phase) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM batch_checkpoints WHERE phase=? ORDER BY batch_num",
                (phase.value,)
            ).fetchall()]

    def get_last_completed_batch(self, phase) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(batch_num) FROM batch_checkpoints WHERE phase=? AND status='completed'",
                (phase.value,)
            ).fetchone()
            return row[0] if row and row[0] is not None else -1
