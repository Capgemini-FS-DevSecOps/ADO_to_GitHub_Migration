"""SQLite state persistence — migration tracking, pipeline inventory, risk scores, gates."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ado2gh.models import (
    MigrationStatus,
    PipelineMetadata,
    RepoConfig,
)
from ado2gh.state.base import StateDBBase
from ado2gh.state.sqlite_agentic_mixin import AgenticPlatformMixin
from ado2gh.state.sqlite_profile_scan_mixin import ProfileScanMixin
from ado2gh.state.sqlite_risk_gates_mixin import RiskGatesMixin
from ado2gh.state.sqlite_users_mixin import PlatformUsersMixin


class SQLiteStateDB(AgenticPlatformMixin, PlatformUsersMixin, ProfileScanMixin, RiskGatesMixin, StateDBBase):
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

    CREATE TABLE IF NOT EXISTS audit_events (
        id               TEXT PRIMARY KEY,
        event_type       TEXT NOT NULL,
        profile_id       TEXT NOT NULL,
        actor            TEXT NOT NULL DEFAULT '',
        assignment_id    TEXT,
        payload_json     TEXT NOT NULL DEFAULT '{}',
        created_at       TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_audit_profile ON audit_events(profile_id, created_at);

    CREATE TABLE IF NOT EXISTS platform_users (
        id               TEXT PRIMARY KEY,
        username         TEXT NOT NULL UNIQUE,
        password_hash    TEXT NOT NULL,
        role             TEXT NOT NULL,
        display_name     TEXT NOT NULL DEFAULT '',
        status           TEXT NOT NULL DEFAULT 'active',
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
        self._mem_conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)
            self._migrate_agentic_columns(conn)
            self._migrate_platform_user_status(conn)

    def _migrate_platform_user_status(self, conn: sqlite3.Connection) -> None:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(platform_users)").fetchall()}
        if cols and "status" not in cols:
            conn.execute(
                "ALTER TABLE platform_users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            )

    def _migrate_agentic_columns(self, conn: sqlite3.Connection):
        """Add assignment_id to migrations when upgrading existing DBs."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(migrations)").fetchall()}
        if "assignment_id" not in cols:
            conn.execute("ALTER TABLE migrations ADD COLUMN assignment_id TEXT")

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
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

    def mark_in_progress_migrations_failed(
        self,
        ado_project: str,
        ado_repo: str,
        *,
        error: str,
    ) -> int:
        """Mark orphaned in_progress scope rows failed (FR-036 stale state)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE migrations SET status='failed', completed_at=?, error_message=?
                   WHERE ado_project=? AND ado_repo=? AND status='in_progress'""",
                (now, error, ado_project, ado_repo),
            )
            return int(cur.rowcount or 0)

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
                """
                SELECT metadata_json FROM pipeline_inventory
                WHERE project=? AND (
                    repo_name=?
                    OR pipeline_name=?
                    OR pipeline_name LIKE ? || '-%'
                    OR pipeline_name LIKE ? || '_%'
                )
                ORDER BY pipeline_id
                """,
                (project, repo_name, repo_name, repo_name, repo_name),
            ).fetchall()
        seen: set[int] = set()
        pipelines: list[PipelineMetadata] = []
        for row in rows:
            meta = PipelineMetadata.from_dict(json.loads(row["metadata_json"]))
            if meta.pipeline_id in seen:
                continue
            seen.add(meta.pipeline_id)
            pipelines.append(meta)
        return pipelines

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
                """
                SELECT COUNT(DISTINCT pipeline_id) FROM pipeline_inventory
                WHERE project=? AND (
                    repo_name=?
                    OR pipeline_name=?
                    OR pipeline_name LIKE ? || '-%'
                    OR pipeline_name LIKE ? || '_%'
                )
                """,
                (project, repo_name, repo_name, repo_name, repo_name),
            ).fetchone()[0]

    def get_latest_repo_migrations(self) -> dict[str, dict]:
        """Latest repo-scope migration row per ADO project/repo."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT m.* FROM migrations m
                INNER JOIN (
                    SELECT ado_project, ado_repo, MAX(id) AS max_id
                    FROM migrations
                    WHERE scope='repo'
                    GROUP BY ado_project, ado_repo
                ) latest ON m.id = latest.max_id
                """
            ).fetchall()
        return {
            f"{r['ado_project']}:{r['ado_repo']}": dict(r)
            for r in rows
        }

    def get_latest_pipeline_migrations(self) -> dict[str, dict]:
        """Latest migration row per project:pipeline_id."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT pm.* FROM pipeline_migrations pm
                INNER JOIN (
                    SELECT project, pipeline_id, MAX(id) AS max_id
                    FROM pipeline_migrations
                    GROUP BY project, pipeline_id
                ) latest
                ON pm.id = latest.max_id
                """
            ).fetchall()
        lookup: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            key = f"{item['project']}:{item['pipeline_id']}"
            lookup[key] = item
        return lookup

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
