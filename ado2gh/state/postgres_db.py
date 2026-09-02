"""PostgreSQL migration state store (production backend)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from ado2gh.models import MigrationStatus, PipelineMetadata
from ado2gh.state.base import StateDBBase
from ado2gh.state.postgres_agentic_users_mixin import PostgresAgenticUsersMixin
from ado2gh.state.postgres_risk_gates_scan_mixin import PostgresRiskGatesScanMixin


def _phase_value(phase) -> str:
    """Accept PhaseType enum or plain phase id string."""
    return phase.value if hasattr(phase, "value") else str(phase)


class PostgresStateDB(PostgresRiskGatesScanMixin, PostgresAgenticUsersMixin, StateDBBase):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS migrations (
        id SERIAL PRIMARY KEY,
        wave_id INTEGER NOT NULL,
        ado_project TEXT NOT NULL,
        ado_repo TEXT NOT NULL,
        gh_org TEXT NOT NULL,
        gh_repo TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        started_at TEXT,
        completed_at TEXT,
        error_message TEXT,
        gh_migration_id TEXT,
        stats TEXT,
        UNIQUE(wave_id, ado_project, ado_repo, scope)
    );
    CREATE TABLE IF NOT EXISTS wave_runs (
        id SERIAL PRIMARY KEY,
        wave_id INTEGER NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        dry_run INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS pipeline_inventory (
        id SERIAL PRIMARY KEY,
        project TEXT NOT NULL,
        pipeline_id INTEGER NOT NULL,
        pipeline_name TEXT NOT NULL,
        pipeline_type TEXT NOT NULL DEFAULT 'yaml',
        repo_id TEXT NOT NULL DEFAULT '',
        repo_name TEXT NOT NULL DEFAULT '',
        folder TEXT NOT NULL DEFAULT '',
        complexity TEXT NOT NULL DEFAULT 'simple',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        scanned_at TEXT,
        UNIQUE(project, pipeline_id)
    );
    CREATE TABLE IF NOT EXISTS pipeline_migrations (
        id SERIAL PRIMARY KEY,
        wave_id INTEGER NOT NULL,
        project TEXT NOT NULL,
        pipeline_id INTEGER NOT NULL,
        pipeline_name TEXT NOT NULL,
        repo_name TEXT NOT NULL,
        gh_org TEXT NOT NULL,
        gh_repo TEXT NOT NULL,
        workflow_file TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        started_at TEXT,
        completed_at TEXT,
        error_message TEXT,
        warnings TEXT,
        unsupported_tasks TEXT,
        complexity TEXT,
        transform_stats TEXT,
        UNIQUE(wave_id, project, pipeline_id)
    );
    CREATE TABLE IF NOT EXISTS repo_risk_scores (
        id SERIAL PRIMARY KEY,
        project TEXT NOT NULL,
        repo_name TEXT NOT NULL,
        total_score DOUBLE PRECISION NOT NULL DEFAULT 0,
        assigned_phase TEXT,
        gh_org TEXT NOT NULL DEFAULT '',
        gh_repo TEXT NOT NULL DEFAULT '',
        score_json TEXT NOT NULL DEFAULT '{}',
        scored_at TEXT,
        UNIQUE(project, repo_name)
    );
    CREATE TABLE IF NOT EXISTS phase_gates (
        id SERIAL PRIMARY KEY,
        phase TEXT NOT NULL UNIQUE,
        status TEXT,
        repo_success_pct DOUBLE PRECISION,
        pipeline_success_pct DOUBLE PRECISION,
        repos_completed INTEGER,
        repos_total INTEGER,
        pipelines_completed INTEGER,
        pipelines_total INTEGER,
        failures_json TEXT,
        override_reason TEXT,
        checked_at TEXT
    );
    CREATE TABLE IF NOT EXISTS batch_checkpoints (
        id SERIAL PRIMARY KEY,
        phase TEXT NOT NULL,
        batch_num INTEGER NOT NULL,
        total_batches INTEGER NOT NULL,
        repos_done INTEGER NOT NULL DEFAULT 0,
        repos_total INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        started_at TEXT,
        completed_at TEXT,
        UNIQUE(phase, batch_num)
    );
    CREATE TABLE IF NOT EXISTS profile_scans (
        id SERIAL PRIMARY KEY,
        profile_id TEXT NOT NULL UNIQUE,
        scanned_at TEXT NOT NULL,
        gh_org TEXT NOT NULL DEFAULT '',
        projects_scanned INTEGER NOT NULL DEFAULT 0,
        repos_scanned INTEGER NOT NULL DEFAULT 0,
        summary_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS profile_scan_repos (
        id SERIAL PRIMARY KEY,
        profile_id TEXT NOT NULL,
        project TEXT NOT NULL,
        repo_name TEXT NOT NULL,
        total_score DOUBLE PRECISION NOT NULL DEFAULT 0,
        suggested_phase TEXT,
        assigned_phase TEXT,
        gh_org TEXT NOT NULL DEFAULT '',
        gh_repo TEXT NOT NULL DEFAULT '',
        pipeline_count INTEGER NOT NULL DEFAULT 0,
        repo_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(profile_id, project, repo_name)
    );
    CREATE INDEX IF NOT EXISTS idx_profile_scan_repos_profile
        ON profile_scan_repos(profile_id);
    CREATE INDEX IF NOT EXISTS idx_profile_scan_repos_phase
        ON profile_scan_repos(profile_id, assigned_phase);
    CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        actor TEXT,
        assignment_id TEXT,
        payload_json TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS platform_users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS auth_sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS live_execution_approvals (
        id TEXT PRIMARY KEY,
        requester_user_id TEXT NOT NULL,
        requester_username TEXT NOT NULL,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        assignment_id TEXT,
        profile_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        reason_request TEXT,
        approver_user_id TEXT,
        approver_username TEXT,
        reason_decision TEXT,
        context_json TEXT,
        requested_at TEXT NOT NULL,
        decided_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_live_approval_scope
        ON live_execution_approvals(scope_type, scope_id, status);

    """

    def __init__(self, dsn: str):
        import psycopg2
        import psycopg2.extras

        self.dsn = dsn
        self._psycopg2 = psycopg2
        self._extras = psycopg2.extras
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator:
        conn = self._psycopg2.connect(self.dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        statements = [
            s.strip() for s in self.SCHEMA.split(";")
            if s.strip() and not s.strip().startswith("--")
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
                cur.execute(
                    "ALTER TABLE platform_users ADD COLUMN IF NOT EXISTS "
                    "status TEXT NOT NULL DEFAULT 'active'",
                )

    def upsert_migration(self, wave_id: int, repo, scope: str,
                         status: MigrationStatus, error: str = None,
                         gh_migration_id: str = None, stats: dict = None):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO migrations
                        (wave_id, ado_project, ado_repo, gh_org, gh_repo, scope,
                         status, started_at, completed_at, error_message,
                         gh_migration_id, stats)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(wave_id, ado_project, ado_repo, scope)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        started_at = COALESCE(migrations.started_at, EXCLUDED.started_at),
                        completed_at = EXCLUDED.completed_at,
                        error_message = EXCLUDED.error_message,
                        gh_migration_id = EXCLUDED.gh_migration_id,
                        stats = EXCLUDED.stats
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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migrations WHERE wave_id=%s ORDER BY id", (wave_id,)
                )
                return [dict(r) for r in cur.fetchall()]

    def get_all_migrations(self) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM migrations ORDER BY wave_id, id")
                return [dict(r) for r in cur.fetchall()]

    def migration_status_counts(self) -> dict:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT status, COUNT(DISTINCT ado_repo) AS cnt "
                    "FROM migrations GROUP BY status"
                )
                return {r["status"]: r["cnt"] for r in cur.fetchall()}

    def get_migration_repo_counts(self) -> dict:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT ado_repo, status FROM migrations
                    GROUP BY ado_project, ado_repo, scope, status
                """)
                rows = cur.fetchall()
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
                cur.execute("SELECT COUNT(*) AS c FROM pipeline_migrations")
                total_pipelines = cur.fetchone()["c"]
                cur.execute(
                    "SELECT COUNT(DISTINCT ado_project || '/' || ado_repo) AS c FROM migrations"
                )
                total_repos = cur.fetchone()["c"]
        return {
            "total_repos": total_repos,
            "completed_repos": done,
            "failed_repos": fail,
            "total_pipelines": total_pipelines,
        }

    def get_failed_migrations(self, wave_id: int = None) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                if wave_id:
                    cur.execute(
                        "SELECT * FROM migrations WHERE wave_id=%s AND status='failed'",
                        (wave_id,),
                    )
                else:
                    cur.execute("SELECT * FROM migrations WHERE status='failed'")
                return [dict(r) for r in cur.fetchall()]

    def wave_summary(self, wave_id: int) -> dict:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT scope, status, COUNT(*) as cnt FROM migrations "
                    "WHERE wave_id=%s GROUP BY scope, status",
                    (wave_id,),
                )
                rows = cur.fetchall()
        result: dict = {}
        for r in rows:
            result.setdefault(r["scope"], {})[r["status"]] = r["cnt"]
        return result

    def mark_wave_run(self, wave_id: int, status: str, dry_run: bool = False) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                if status == "started":
                    cur.execute(
                        "INSERT INTO wave_runs (wave_id, started_at, status, dry_run) "
                        "VALUES (%s,%s,%s,%s) RETURNING id",
                        (wave_id, now, "in_progress", int(dry_run)),
                    )
                    return cur.fetchone()[0]
                cur.execute(
                    "UPDATE wave_runs SET completed_at=%s, status=%s "
                    "WHERE wave_id=%s AND completed_at IS NULL",
                    (now, status, wave_id),
                )
                return -1

    def upsert_pipeline_inventory(self, meta: PipelineMetadata):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pipeline_inventory
                        (project, pipeline_id, pipeline_name, pipeline_type,
                         repo_id, repo_name, folder, complexity, metadata_json, scanned_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(project, pipeline_id)
                    DO UPDATE SET
                        pipeline_name = EXCLUDED.pipeline_name,
                        pipeline_type = EXCLUDED.pipeline_type,
                        repo_id = EXCLUDED.repo_id,
                        repo_name = EXCLUDED.repo_name,
                        folder = EXCLUDED.folder,
                        complexity = EXCLUDED.complexity,
                        metadata_json = EXCLUDED.metadata_json,
                        scanned_at = EXCLUDED.scanned_at
                """, (
                    meta.project, meta.pipeline_id, meta.pipeline_name,
                    meta.pipeline_type.value, meta.repo_id, meta.repo_name,
                    meta.folder, meta.complexity.value,
                    json.dumps(meta.to_dict()), now,
                ))

    def get_pipelines_for_repo(self, project: str, repo_name: str) -> list[PipelineMetadata]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT metadata_json FROM pipeline_inventory
                    WHERE project=%s AND (
                        repo_name=%s
                        OR pipeline_name=%s
                        OR pipeline_name LIKE %s || '-%%'
                        OR pipeline_name LIKE %s || '_%%'
                    )
                    ORDER BY pipeline_id
                    """,
                    (project, repo_name, repo_name, repo_name, repo_name),
                )
                rows = cur.fetchall()
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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                if project:
                    cur.execute(
                        "SELECT * FROM pipeline_inventory WHERE project=%s ORDER BY pipeline_id",
                        (project,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM pipeline_inventory ORDER BY project, pipeline_id"
                    )
                return [dict(r) for r in cur.fetchall()]

    def inventory_count(self, project: str = None) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if project:
                    cur.execute(
                        "SELECT COUNT(*) FROM pipeline_inventory WHERE project=%s",
                        (project,),
                    )
                else:
                    cur.execute("SELECT COUNT(*) FROM pipeline_inventory")
                return cur.fetchone()[0]

    def inventory_count_for_repo(self, project: str, repo_name: str) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT pipeline_id) FROM pipeline_inventory
                    WHERE project=%s AND (
                        repo_name=%s
                        OR pipeline_name=%s
                        OR pipeline_name LIKE %s || '-%%'
                        OR pipeline_name LIKE %s || '_%%'
                    )
                    """,
                    (project, repo_name, repo_name, repo_name, repo_name),
                )
                return cur.fetchone()[0]

    def get_latest_repo_migrations(self) -> dict[str, dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT m.* FROM migrations m
                    INNER JOIN (
                        SELECT ado_project, ado_repo, MAX(id) AS max_id
                        FROM migrations
                        WHERE scope='repo'
                        GROUP BY ado_project, ado_repo
                    ) latest ON m.id = latest.max_id
                    """
                )
                rows = cur.fetchall()
        return {
            f"{r['ado_project']}:{r['ado_repo']}": dict(r)
            for r in rows
        }

    def get_latest_pipeline_migrations(self) -> dict[str, dict]:
        """Latest migration row per project:pipeline_id."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT pm.* FROM pipeline_migrations pm
                    INNER JOIN (
                        SELECT project, pipeline_id, MAX(id) AS max_id
                        FROM pipeline_migrations
                        GROUP BY project, pipeline_id
                    ) latest
                    ON pm.id = latest.max_id
                    """
                )
                rows = cur.fetchall()
        lookup: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            key = f"{item['project']}:{item['pipeline_id']}"
            lookup[key] = item
        return lookup

    def clear_inventory(self, project: str = None):
        with self._conn() as conn:
            with conn.cursor() as cur:
                if project:
                    cur.execute("DELETE FROM pipeline_inventory WHERE project=%s", (project,))
                else:
                    cur.execute("DELETE FROM pipeline_inventory")

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
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pipeline_migrations
                        (wave_id, project, pipeline_id, pipeline_name, repo_name,
                         gh_org, gh_repo, workflow_file, status,
                         started_at, completed_at, error_message,
                         warnings, unsupported_tasks, complexity, transform_stats)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(wave_id, project, pipeline_id)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        workflow_file = EXCLUDED.workflow_file,
                        started_at = COALESCE(pipeline_migrations.started_at, EXCLUDED.started_at),
                        completed_at = EXCLUDED.completed_at,
                        error_message = EXCLUDED.error_message,
                        warnings = EXCLUDED.warnings,
                        unsupported_tasks = EXCLUDED.unsupported_tasks,
                        transform_stats = EXCLUDED.transform_stats
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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM pipeline_migrations WHERE wave_id=%s ORDER BY id",
                    (wave_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_failed_pipeline_migrations(self, wave_id: int) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM pipeline_migrations "
                    "WHERE wave_id=%s AND status IN ('failed','pending') ORDER BY id",
                    (wave_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def pipeline_migration_summary(self, wave_id: int) -> dict:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT status, complexity, COUNT(*) as cnt "
                    "FROM pipeline_migrations WHERE wave_id=%s "
                    "GROUP BY status, complexity",
                    (wave_id,),
                )
                rows = cur.fetchall()
        result: dict = {"by_status": {}, "by_complexity": {}}
        for r in rows:
            result["by_status"][r["status"]] = \
                result["by_status"].get(r["status"], 0) + r["cnt"]
            result["by_complexity"][r["complexity"]] = \
                result["by_complexity"].get(r["complexity"], 0) + r["cnt"]
        return result

    def reset_failed_pipeline_migrations(self, wave_id: int):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM pipeline_migrations WHERE wave_id=%s AND status='failed'",
                    (wave_id,),
                )
