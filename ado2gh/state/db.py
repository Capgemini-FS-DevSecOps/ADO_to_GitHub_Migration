"""SQLite state persistence — migration tracking, pipeline inventory, risk scores, gates."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

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

    CREATE TABLE IF NOT EXISTS profile_scans (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id       TEXT NOT NULL UNIQUE,
        scanned_at       TEXT NOT NULL,
        gh_org           TEXT NOT NULL DEFAULT '',
        projects_scanned INTEGER NOT NULL DEFAULT 0,
        repos_scanned    INTEGER NOT NULL DEFAULT 0,
        summary_json     TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS profile_scan_repos (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id       TEXT NOT NULL,
        project          TEXT NOT NULL,
        repo_name        TEXT NOT NULL,
        total_score      REAL NOT NULL DEFAULT 0,
        suggested_phase  TEXT,
        assigned_phase   TEXT,
        gh_org           TEXT NOT NULL DEFAULT '',
        gh_repo          TEXT NOT NULL DEFAULT '',
        pipeline_count   INTEGER NOT NULL DEFAULT 0,
        repo_json        TEXT NOT NULL DEFAULT '{}',
        UNIQUE(profile_id, project, repo_name)
    );

    CREATE INDEX IF NOT EXISTS idx_profile_scan_repos_profile
        ON profile_scan_repos(profile_id);
    CREATE INDEX IF NOT EXISTS idx_profile_scan_repos_phase
        ON profile_scan_repos(profile_id, assigned_phase);

    CREATE TABLE IF NOT EXISTS migration_assignments (
        id               TEXT PRIMARY KEY,
        profile_id       TEXT NOT NULL,
        name             TEXT NOT NULL,
        assignment_type  TEXT NOT NULL,
        execution_phase  TEXT NOT NULL,
        wave_number      INTEGER,
        status           TEXT NOT NULL DEFAULT 'active',
        created_by       TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS cohort_membership (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id    TEXT NOT NULL,
        profile_id       TEXT NOT NULL,
        ado_project      TEXT NOT NULL,
        ado_repo         TEXT NOT NULL,
        gh_org           TEXT NOT NULL DEFAULT '',
        gh_repo          TEXT NOT NULL DEFAULT '',
        active           INTEGER NOT NULL DEFAULT 1,
        UNIQUE(profile_id, ado_project, ado_repo, active)
    );

    CREATE TABLE IF NOT EXISTS repo_dependency_edges (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id       TEXT NOT NULL,
        from_repo        TEXT NOT NULL,
        to_repo          TEXT NOT NULL,
        edge_type        TEXT NOT NULL DEFAULT 'pipeline_resource',
        source_pipeline_id INTEGER,
        discovered_at    TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_events (
        id               TEXT PRIMARY KEY,
        event_type       TEXT NOT NULL,
        profile_id       TEXT NOT NULL,
        actor            TEXT NOT NULL DEFAULT '',
        assignment_id    TEXT,
        payload_json     TEXT NOT NULL DEFAULT '{}',
        created_at       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS workflow_dependency_checks (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id       TEXT NOT NULL,
        repo_key         TEXT NOT NULL,
        check_json       TEXT NOT NULL DEFAULT '{}',
        checked_at       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS remediation_loops (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id       TEXT NOT NULL,
        repo_key         TEXT NOT NULL,
        retry_count      INTEGER NOT NULL DEFAULT 0,
        max_retries      INTEGER NOT NULL DEFAULT 3,
        status           TEXT NOT NULL DEFAULT 'active',
        updated_at       TEXT NOT NULL,
        UNIQUE(session_id, repo_key)
    );

    CREATE INDEX IF NOT EXISTS idx_audit_profile ON audit_events(profile_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_cohort_assignment ON cohort_membership(assignment_id);

    CREATE TABLE IF NOT EXISTS platform_users (
        id               TEXT PRIMARY KEY,
        username         TEXT NOT NULL UNIQUE,
        password_hash    TEXT NOT NULL,
        role             TEXT NOT NULL,
        display_name     TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS auth_sessions (
        token            TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL,
        expires_at       TEXT NOT NULL,
        created_at       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS live_execution_approvals (
        id                   TEXT PRIMARY KEY,
        requester_user_id    TEXT NOT NULL,
        requester_username   TEXT NOT NULL,
        scope_type           TEXT NOT NULL,
        scope_id             TEXT NOT NULL,
        assignment_id        TEXT,
        profile_id           TEXT,
        status               TEXT NOT NULL DEFAULT 'pending',
        reason_request       TEXT,
        approver_user_id     TEXT,
        approver_username    TEXT,
        reason_decision      TEXT,
        context_json         TEXT,
        requested_at         TEXT NOT NULL,
        decided_at           TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_live_approval_scope
        ON live_execution_approvals(scope_type, scope_id, status);
    """

    def __init__(self, db_path: str = "migration_state.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)
            self._migrate_agentic_columns(conn)

    def _migrate_agentic_columns(self, conn: sqlite3.Connection):
        """Add assignment_id to migrations when upgrading existing DBs."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(migrations)").fetchall()}
        if "assignment_id" not in cols:
            conn.execute("ALTER TABLE migrations ADD COLUMN assignment_id TEXT")

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

    def migration_status_counts(self) -> dict:
        """Aggregated repo counts by migration status (distinct ado_repo)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(DISTINCT ado_repo) AS cnt "
                "FROM migrations GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def get_migration_repo_counts(self) -> dict:
        """Aggregated repo migration counts — avoids loading full migrations table."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ado_repo, status FROM migrations
                GROUP BY ado_project, ado_repo, scope, status
            """).fetchall()
            done = fail = 0
            seen_done: set[str] = set()
            seen_fail: set[str] = set()
            for r in rows:
                key = r["ado_repo"]
                if r["status"] == "completed":
                    seen_done.add(key)
                elif r["status"] == "failed":
                    seen_fail.add(key)
            done = len(seen_done - seen_fail)
            fail = len(seen_fail - seen_done)
            total_pipelines = conn.execute(
                "SELECT COUNT(*) FROM pipeline_migrations"
            ).fetchone()[0]
            total_repos = conn.execute(
                "SELECT COUNT(DISTINCT ado_project || '/' || ado_repo) FROM migrations"
            ).fetchone()[0]
        return {
            "total_repos": total_repos,
            "completed_repos": done,
            "failed_repos": fail,
            "total_pipelines": total_pipelines,
        }

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

    def prune_risk_scores_not_in(self, keys: set[tuple[str, str]]) -> int:
        """Remove risk-score rows not present in the latest discovery scan."""
        if not keys:
            return 0
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT project, repo_name FROM repo_risk_scores"
            ).fetchall()
            removed = 0
            for project, repo_name in rows:
                if (project, repo_name) not in keys:
                    conn.execute(
                        "DELETE FROM repo_risk_scores WHERE project=? AND repo_name=?",
                        (project, repo_name),
                    )
                    removed += 1
        return removed

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
                score.assigned_phase if score.assigned_phase else None,
                score.gh_org, score.gh_repo,
                json.dumps(score.to_dict()), now,
            ))

    def get_all_risk_scores(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM repo_risk_scores ORDER BY total_score"
            ).fetchall()]

    def get_risk_scores_for_phase(self, phase) -> list:
        phase_val = phase.value if hasattr(phase, "value") else str(phase)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM repo_risk_scores WHERE assigned_phase=? ORDER BY total_score",
                (phase_val,)
            ).fetchall()]

    def count_repos_by_phase(self, phase_id: str, profile_id: str | None = None) -> dict[str, int]:
        counts: dict[str, int] = {"risk_scores": 0, "profile_scan": 0}
        with self._conn() as conn:
            counts["risk_scores"] = conn.execute(
                "SELECT COUNT(*) FROM repo_risk_scores WHERE assigned_phase=?",
                (phase_id,),
            ).fetchone()[0]
            if profile_id:
                counts["profile_scan"] = conn.execute(
                    "SELECT COUNT(*) FROM profile_scan_repos "
                    "WHERE profile_id=? AND assigned_phase=?",
                    (profile_id, phase_id),
                ).fetchone()[0]
            else:
                counts["profile_scan"] = conn.execute(
                    "SELECT COUNT(*) FROM profile_scan_repos WHERE assigned_phase=?",
                    (phase_id,),
                ).fetchone()[0]
        return counts

    def reassign_phase_repos(
        self,
        from_phase: str,
        to_phase: str,
        profile_id: str | None = None,
    ) -> dict[str, int]:
        updated = {"risk_scores": 0, "profile_scan": 0}
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE repo_risk_scores SET assigned_phase=? WHERE assigned_phase=?",
                (to_phase, from_phase),
            )
            updated["risk_scores"] = cur.rowcount
            if profile_id:
                cur = conn.execute(
                    "UPDATE profile_scan_repos SET assigned_phase=? "
                    "WHERE profile_id=? AND assigned_phase=?",
                    (to_phase, profile_id, from_phase),
                )
            else:
                cur = conn.execute(
                    "UPDATE profile_scan_repos SET assigned_phase=? WHERE assigned_phase=?",
                    (to_phase, from_phase),
                )
            updated["profile_scan"] = cur.rowcount
        return updated

    def scan_repo_scores(self, profile_id: str | None = None) -> list[float]:
        with self._conn() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT total_score FROM profile_scan_repos WHERE profile_id=?",
                    (profile_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT total_score FROM profile_scan_repos").fetchall()
            if rows:
                return [float(r[0]) for r in rows]
            rows = conn.execute("SELECT total_score FROM repo_risk_scores").fetchall()
        return [float(r[0]) for r in rows]

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

    # ── Profile scan (discovery per migration profile) ───────────────────────

    def save_profile_scan(self, profile_id: str, raw: dict[str, Any]) -> None:
        now = raw.get("scanned_at") or datetime.now(timezone.utc).isoformat()
        gh_org = raw.get("gh_org", "")
        summary = {
            k: {kk: vv for kk, vv in v.items() if kk != "repos"}
            for k, v in raw.get("recommendations", {}).items()
        }
        with self._conn() as conn:
            conn.execute("DELETE FROM profile_scan_repos WHERE profile_id=?", (profile_id,))
            conn.execute("""
                INSERT INTO profile_scans
                    (profile_id, scanned_at, gh_org, projects_scanned, repos_scanned, summary_json)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    scanned_at=excluded.scanned_at,
                    gh_org=excluded.gh_org,
                    projects_scanned=excluded.projects_scanned,
                    repos_scanned=excluded.repos_scanned,
                    summary_json=excluded.summary_json
            """, (
                profile_id, now, gh_org,
                raw.get("projects_scanned", 0),
                raw.get("repos_scanned", 0),
                json.dumps(summary),
            ))
            for phase_key, bucket in raw.get("recommendations", {}).items():
                for repo in bucket.get("repos", []):
                    suggested = repo.get("assigned_phase") or phase_key
                    conn.execute("""
                        INSERT INTO profile_scan_repos
                            (profile_id, project, repo_name, total_score, suggested_phase,
                             assigned_phase, gh_org, gh_repo, pipeline_count, repo_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        profile_id,
                        repo.get("project", ""),
                        repo.get("repo_name", ""),
                        repo.get("total_score", 0),
                        suggested,
                        suggested,
                        repo.get("gh_org", gh_org),
                        repo.get("gh_repo", repo.get("repo_name", "")),
                        repo.get("pipeline_count", 0),
                        json.dumps(repo),
                    ))

    def get_profile_scan_meta(self, profile_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM profile_scans WHERE profile_id=?", (profile_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_profile_scan_repos(
        self, profile_id: str, phase: str | None = None,
    ) -> list[dict]:
        with self._conn() as conn:
            if phase:
                rows = conn.execute(
                    "SELECT * FROM profile_scan_repos WHERE profile_id=? AND assigned_phase=? "
                    "ORDER BY total_score, project, repo_name",
                    (profile_id, phase),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM profile_scan_repos WHERE profile_id=? "
                    "ORDER BY assigned_phase, total_score, project, repo_name",
                    (profile_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def update_profile_repo_phases(
        self, profile_id: str, assignments: list[dict[str, str]],
    ) -> int:
        updated = 0
        with self._conn() as conn:
            for item in assignments:
                cur = conn.execute(
                    "UPDATE profile_scan_repos SET assigned_phase=? "
                    "WHERE profile_id=? AND project=? AND repo_name=?",
                    (
                        item["assigned_phase"],
                        profile_id,
                        item["project"],
                        item["repo_name"],
                    ),
                )
                updated += cur.rowcount
        return updated

    def build_profile_scan_payload(self, profile_id: str) -> Optional[dict[str, Any]]:
        meta = self.get_profile_scan_meta(profile_id)
        if not meta:
            return None
        repos = self.get_profile_scan_repos(profile_id)
        buckets: dict[str, dict] = {}
        for r in repos:
            phase = r.get("assigned_phase") or r.get("suggested_phase") or "unassigned"
            if phase not in buckets:
                buckets[phase] = {
                    "phase": phase,
                    "repo_count": 0,
                    "risk_min": r["total_score"],
                    "risk_max": r["total_score"],
                    "rationale": f"User-assigned and recommended repos in {phase}",
                    "repos": [],
                }
            b = buckets[phase]
            b["repo_count"] += 1
            b["risk_min"] = min(b["risk_min"], r["total_score"])
            b["risk_max"] = max(b["risk_max"], r["total_score"])
            repo_data = json.loads(r.get("repo_json") or "{}")
            repo_data["assigned_phase"] = r.get("assigned_phase")
            repo_data["suggested_phase"] = r.get("suggested_phase")
            b["repos"].append(repo_data)
        return {
            "profile_id": profile_id,
            "scanned_at": meta["scanned_at"],
            "projects_scanned": meta["projects_scanned"],
            "repos_scanned": meta["repos_scanned"],
            "total_repos": meta["repos_scanned"],
            "gh_org": meta["gh_org"],
            "recommendations": buckets,
        }

    # ── Agentic platform (assignments, audit, remediation) ─────────────────

    def insert_assignment(
        self,
        id: str,
        profile_id: str,
        name: str,
        assignment_type: str,
        execution_phase: str,
        wave_number: int | None,
        status: str,
        created_by: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO migration_assignments
                (id, profile_id, name, assignment_type, execution_phase,
                 wave_number, status, created_by, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    id, profile_id, name, assignment_type, execution_phase,
                    wave_number, status, created_by, now,
                ),
            )

    def get_assignment(self, assignment_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM migration_assignments WHERE id=?", (assignment_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_assignments(self, profile_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM migration_assignments WHERE profile_id=? ORDER BY created_at",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_cohort_membership(
        self,
        assignment_id: str,
        profile_id: str,
        ado_project: str,
        ado_repo: str,
        gh_org: str,
        gh_repo: str,
        active: bool,
    ):
        with self._conn() as conn:
            if active:
                conn.execute(
                    "UPDATE cohort_membership SET active=0 "
                    "WHERE profile_id=? AND ado_project=? AND ado_repo=? AND active=1",
                    (profile_id, ado_project, ado_repo),
                )
            conn.execute(
                """
                INSERT INTO cohort_membership
                (assignment_id, profile_id, ado_project, ado_repo, gh_org, gh_repo, active)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    assignment_id, profile_id, ado_project, ado_repo,
                    gh_org, gh_repo, 1 if active else 0,
                ),
            )

    def get_cohort_repos(self, assignment_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ado_project, ado_repo, gh_org, gh_repo FROM cohort_membership "
                "WHERE assignment_id=? AND active=1",
                (assignment_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        assignment_id: str | None,
        payload_json: str,
        created_at: str,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                (id, event_type, profile_id, actor, assignment_id, payload_json, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    event_id, event_type, profile_id, actor,
                    assignment_id, payload_json, created_at,
                ),
            )

    def list_audit_events(
        self, profile_id: str | None = None, limit: int = 100,
    ) -> list[dict]:
        with self._conn() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM audit_events WHERE profile_id=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (profile_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def upsert_remediation_loop(
        self, session_id: str, repo_key: str, retry_count: int,
        max_retries: int, status: str,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO remediation_loops
                (session_id, repo_key, retry_count, max_retries, status, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(session_id, repo_key) DO UPDATE SET
                    retry_count=excluded.retry_count,
                    max_retries=excluded.max_retries,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (session_id, repo_key, retry_count, max_retries, status, now),
            )

    def get_dependency_edges(self, profile_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM repo_dependency_edges WHERE profile_id=?",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_dependency_edge(
        self, profile_id: str, from_repo: str, to_repo: str,
        edge_type: str = "pipeline_resource",
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO repo_dependency_edges
                (profile_id, from_repo, to_repo, edge_type, discovered_at)
                VALUES (?,?,?,?,?)
                """,
                (profile_id, from_repo, to_repo, edge_type, now),
            )

    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM migrations WHERE ado_project=? AND ado_repo=? "
                "AND status='in_progress' LIMIT 1",
                (ado_project, ado_repo),
            ).fetchone()
        return row is not None

    def is_repo_in_assignment_cohort(
        self, assignment_id: str, ado_project: str, ado_repo: str,
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM cohort_membership WHERE assignment_id=? "
                "AND ado_project=? AND ado_repo=? AND active=1 LIMIT 1",
                (assignment_id, ado_project, ado_repo),
            ).fetchone()
        return row is not None

    def get_remediation_loop(self, session_id: str, repo_key: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM remediation_loops WHERE session_id=? AND repo_key=?",
                (session_id, repo_key),
            ).fetchone()
        return dict(row) if row else None

    def count_platform_users(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM platform_users").fetchone()
        return int(row["c"]) if row else 0

    def create_platform_user(
        self, user_id: str, username: str, password_hash: str,
        role: str, display_name: str, created_at: str,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO platform_users (id, username, password_hash, role, display_name, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (user_id, username, password_hash, role, display_name, created_at),
            )

    def get_platform_user_by_username(self, username: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM platform_users WHERE username=?",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def get_platform_user_by_id(self, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM platform_users WHERE id=?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_platform_users(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, username, role, display_name, created_at FROM platform_users ORDER BY username",
            ).fetchall()
        return [dict(r) for r in rows]

    def create_auth_session(self, token: str, user_id: str, expires_at: str, created_at: str):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (token, user_id, expires_at, created_at)
                VALUES (?,?,?,?)
                """,
                (token, user_id, expires_at, created_at),
            )

    def get_auth_session(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE token=?",
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def delete_auth_session(self, token: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))

    def create_live_execution_approval(
        self,
        approval_id: str,
        requester_user_id: str,
        requester_username: str,
        scope_type: str,
        scope_id: str,
        requested_at: str,
        assignment_id: str | None = None,
        profile_id: str | None = None,
        reason_request: str | None = None,
        context_json: str | None = None,
    ) -> dict:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO live_execution_approvals (
                    id, requester_user_id, requester_username, scope_type, scope_id,
                    assignment_id, profile_id, status, reason_request, context_json,
                    requested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    approval_id, requester_user_id, requester_username,
                    scope_type, scope_id, assignment_id, profile_id,
                    "pending", reason_request, context_json, requested_at,
                ),
            )
        return self.get_live_execution_approval(approval_id) or {}

    def get_live_execution_approval(self, approval_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM live_execution_approvals WHERE id=?",
                (approval_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_pending_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_execution_approvals
                WHERE scope_type=? AND scope_id=? AND status='pending'
                ORDER BY requested_at DESC LIMIT 1
                """,
                (scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    def find_approved_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_execution_approvals
                WHERE scope_type=? AND scope_id=? AND status='approved'
                ORDER BY decided_at DESC LIMIT 1
                """,
                (scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    def list_live_execution_approvals(
        self, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        with self._conn() as conn:
            if status and status != "all":
                rows = conn.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE status=?
                    ORDER BY requested_at DESC LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    ORDER BY requested_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def decide_live_execution_approval(
        self,
        approval_id: str,
        status: str,
        approver_user_id: str,
        approver_username: str,
        reason_decision: str,
        decided_at: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM live_execution_approvals WHERE id=?",
                (approval_id,),
            ).fetchone()
            if not row:
                return None
            if row["status"] != "pending":
                return self.get_live_execution_approval(approval_id)
            conn.execute(
                """
                UPDATE live_execution_approvals
                SET status=?, approver_user_id=?, approver_username=?,
                    reason_decision=?, decided_at=?
                WHERE id=?
                """,
                (
                    status, approver_user_id, approver_username,
                    reason_decision, decided_at, approval_id,
                ),
            )
        return self.get_live_execution_approval(approval_id)
