"""PostgreSQL migration state store (production backend)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from ado2gh.models import MigrationStatus, PipelineMetadata


def _phase_value(phase) -> str:
    """Accept PhaseType enum or plain phase id string."""
    return phase.value if hasattr(phase, "value") else str(phase)


class PostgresStateDB:
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
    CREATE TABLE IF NOT EXISTS migration_assignments (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        name TEXT NOT NULL,
        assignment_type TEXT NOT NULL,
        execution_phase TEXT NOT NULL,
        wave_number INTEGER,
        status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cohort_membership (
        id SERIAL PRIMARY KEY,
        assignment_id TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        ado_project TEXT NOT NULL,
        ado_repo TEXT NOT NULL,
        gh_org TEXT,
        gh_repo TEXT,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS repo_dependency_edges (
        id SERIAL PRIMARY KEY,
        profile_id TEXT NOT NULL,
        from_repo TEXT NOT NULL,
        to_repo TEXT NOT NULL,
        edge_type TEXT NOT NULL DEFAULT 'pipeline_resource',
        discovered_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        actor TEXT,
        assignment_id TEXT,
        payload_json TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS remediation_loops (
        id SERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        repo_key TEXT NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        max_retries INTEGER NOT NULL DEFAULT 3,
        status TEXT NOT NULL DEFAULT 'active',
        updated_at TEXT NOT NULL,
        UNIQUE(session_id, repo_key)
    );
    CREATE TABLE IF NOT EXISTS platform_users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
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
                    "SELECT metadata_json FROM pipeline_inventory "
                    "WHERE project=%s AND repo_name=%s ORDER BY pipeline_id",
                    (project, repo_name),
                )
                rows = cur.fetchall()
        return [PipelineMetadata.from_dict(json.loads(r["metadata_json"])) for r in rows]

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
                    "SELECT COUNT(*) FROM pipeline_inventory WHERE project=%s AND repo_name=%s",
                    (project, repo_name),
                )
                return cur.fetchone()[0]

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

    def upsert_risk_score(self, score):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO repo_risk_scores
                        (project,repo_name,total_score,assigned_phase,gh_org,gh_repo,score_json,scored_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(project,repo_name) DO UPDATE SET
                        total_score=EXCLUDED.total_score,
                        assigned_phase=EXCLUDED.assigned_phase,
                        gh_org=EXCLUDED.gh_org, gh_repo=EXCLUDED.gh_repo,
                        score_json=EXCLUDED.score_json, scored_at=EXCLUDED.scored_at
                """, (
                    score.project, score.repo_name, score.total_score,
                    score.assigned_phase if score.assigned_phase else None,
                    score.gh_org, score.gh_repo,
                    json.dumps(score.to_dict()), now,
                ))

    def get_all_risk_scores(self) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM repo_risk_scores ORDER BY total_score")
                return [dict(r) for r in cur.fetchall()]

    def get_risk_scores_for_phase(self, phase) -> list:
        phase_val = _phase_value(phase)
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM repo_risk_scores WHERE assigned_phase=%s ORDER BY total_score",
                    (phase_val,),
                )
                return [dict(r) for r in cur.fetchall()]

    def risk_score_count(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM repo_risk_scores")
                return cur.fetchone()[0]

    def count_repos_by_phase(self, phase_id: str, profile_id: str | None = None) -> dict[str, int]:
        counts: dict[str, int] = {"risk_scores": 0, "profile_scan": 0}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM repo_risk_scores WHERE assigned_phase=%s",
                    (phase_id,),
                )
                counts["risk_scores"] = cur.fetchone()[0]
                if profile_id:
                    cur.execute(
                        "SELECT COUNT(*) FROM profile_scan_repos "
                        "WHERE profile_id=%s AND assigned_phase=%s",
                        (profile_id, phase_id),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) FROM profile_scan_repos WHERE assigned_phase=%s",
                        (phase_id,),
                    )
                counts["profile_scan"] = cur.fetchone()[0]
        return counts

    def reassign_phase_repos(
        self,
        from_phase: str,
        to_phase: str,
        profile_id: str | None = None,
    ) -> dict[str, int]:
        updated = {"risk_scores": 0, "profile_scan": 0}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE repo_risk_scores SET assigned_phase=%s WHERE assigned_phase=%s",
                    (to_phase, from_phase),
                )
                updated["risk_scores"] = cur.rowcount
                if profile_id:
                    cur.execute(
                        "UPDATE profile_scan_repos SET assigned_phase=%s "
                        "WHERE profile_id=%s AND assigned_phase=%s",
                        (to_phase, profile_id, from_phase),
                    )
                else:
                    cur.execute(
                        "UPDATE profile_scan_repos SET assigned_phase=%s WHERE assigned_phase=%s",
                        (to_phase, from_phase),
                    )
                updated["profile_scan"] = cur.rowcount
        return updated

    def scan_repo_scores(self, profile_id: str | None = None) -> list[float]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if profile_id:
                    cur.execute(
                        "SELECT total_score FROM profile_scan_repos WHERE profile_id=%s",
                        (profile_id,),
                    )
                else:
                    cur.execute("SELECT total_score FROM profile_scan_repos")
                rows = cur.fetchall()
                if rows:
                    return [float(r[0]) for r in rows]
                cur.execute("SELECT total_score FROM repo_risk_scores")
                rows = cur.fetchall()
        return [float(r[0]) for r in rows]

    # ── Profile scan (discovery per migration profile) ───────────────────────

    def save_profile_scan(self, profile_id: str, raw: dict[str, Any]) -> None:
        now = raw.get("scanned_at") or datetime.now(timezone.utc).isoformat()
        gh_org = raw.get("gh_org", "")
        summary = {
            k: {kk: vv for kk, vv in v.items() if kk != "repos"}
            for k, v in raw.get("recommendations", {}).items()
        }
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM profile_scan_repos WHERE profile_id=%s",
                    (profile_id,),
                )
                cur.execute("""
                    INSERT INTO profile_scans
                        (profile_id, scanned_at, gh_org, projects_scanned, repos_scanned, summary_json)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        scanned_at=EXCLUDED.scanned_at,
                        gh_org=EXCLUDED.gh_org,
                        projects_scanned=EXCLUDED.projects_scanned,
                        repos_scanned=EXCLUDED.repos_scanned,
                        summary_json=EXCLUDED.summary_json
                """, (
                    profile_id, now, gh_org,
                    raw.get("projects_scanned", 0),
                    raw.get("repos_scanned", 0),
                    json.dumps(summary),
                ))
                for phase_key, bucket in raw.get("recommendations", {}).items():
                    for repo in bucket.get("repos", []):
                        suggested = repo.get("assigned_phase") or phase_key
                        cur.execute("""
                            INSERT INTO profile_scan_repos
                                (profile_id, project, repo_name, total_score, suggested_phase,
                                 assigned_phase, gh_org, gh_repo, pipeline_count, repo_json)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM profile_scans WHERE profile_id=%s",
                    (profile_id,),
                )
                row = cur.fetchone()
        return dict(row) if row else None

    def get_profile_scan_repos(
        self, profile_id: str, phase: str | None = None,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                if phase:
                    cur.execute(
                        "SELECT * FROM profile_scan_repos WHERE profile_id=%s AND assigned_phase=%s "
                        "ORDER BY total_score, project, repo_name",
                        (profile_id, phase),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM profile_scan_repos WHERE profile_id=%s "
                        "ORDER BY assigned_phase, total_score, project, repo_name",
                        (profile_id,),
                    )
                return [dict(r) for r in cur.fetchall()]

    def update_profile_repo_phases(
        self, profile_id: str, assignments: list[dict[str, str]],
    ) -> int:
        updated = 0
        with self._conn() as conn:
            with conn.cursor() as cur:
                for item in assignments:
                    cur.execute(
                        "UPDATE profile_scan_repos SET assigned_phase=%s "
                        "WHERE profile_id=%s AND project=%s AND repo_name=%s",
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

    def upsert_phase_gate(self, result):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO phase_gates
                        (phase,status,repo_success_pct,pipeline_success_pct,
                         repos_completed,repos_total,pipelines_completed,pipelines_total,
                         failures_json,override_reason,checked_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(phase) DO UPDATE SET
                        status=EXCLUDED.status,
                        repo_success_pct=EXCLUDED.repo_success_pct,
                        pipeline_success_pct=EXCLUDED.pipeline_success_pct,
                        repos_completed=EXCLUDED.repos_completed,
                        repos_total=EXCLUDED.repos_total,
                        pipelines_completed=EXCLUDED.pipelines_completed,
                        pipelines_total=EXCLUDED.pipelines_total,
                        failures_json=EXCLUDED.failures_json,
                        override_reason=EXCLUDED.override_reason,
                        checked_at=EXCLUDED.checked_at
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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM phase_gates WHERE phase=%s", (_phase_value(phase),))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_all_phase_gates(self) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM phase_gates ORDER BY id")
                return [dict(r) for r in cur.fetchall()]

    def upsert_batch_checkpoint(self, cp):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO batch_checkpoints
                        (phase,batch_num,total_batches,repos_done,repos_total,status,started_at,completed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(phase,batch_num) DO UPDATE SET
                        repos_done=EXCLUDED.repos_done, status=EXCLUDED.status,
                        completed_at=EXCLUDED.completed_at
                """, (
                    cp.phase.value, cp.batch_num, cp.total_batches,
                    cp.repos_done, cp.repos_total, cp.status,
                    cp.started_at or now, cp.completed_at,
                ))

    def get_batch_checkpoints(self, phase) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM batch_checkpoints WHERE phase=%s ORDER BY batch_num",
                    (_phase_value(phase),),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_last_completed_batch(self, phase) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(batch_num) FROM batch_checkpoints "
                    "WHERE phase=%s AND status='completed'",
                    (_phase_value(phase),),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] is not None else -1

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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events
                    (id, event_type, profile_id, actor, assignment_id, payload_json, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        event_id, event_type, profile_id, actor,
                        assignment_id, payload_json, created_at,
                    ),
                )

    def list_audit_events(
        self, profile_id: str | None = None, limit: int = 100,
    ) -> list[dict]:
        return self.search_audit_events(
            profile_id=profile_id, limit=limit, offset=0,
        )

    def search_audit_events(
        self,
        *,
        profile_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        actor: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        from ado2gh.state.audit_query import AuditEventFilters, build_audit_filters

        filters = AuditEventFilters(
            profile_id=profile_id,
            actor=actor,
            event_type=event_type,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        clauses, params = build_audit_filters(filters)
        where = " AND ".join(clauses).replace("?", "%s")
        sql = (
            f"SELECT * FROM audit_events WHERE {where} "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s"
        )
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, (*params, limit, offset))
                return [dict(r) for r in cur.fetchall()]

    def count_audit_events(
        self,
        *,
        profile_id: str | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> int:
        from ado2gh.state.audit_query import AuditEventFilters, build_audit_filters

        filters = AuditEventFilters(
            profile_id=profile_id,
            actor=actor,
            event_type=event_type,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        clauses, params = build_audit_filters(filters)
        where = " AND ".join(clauses).replace("?", "%s")
        sql = f"SELECT COUNT(*) AS c FROM audit_events WHERE {where}"
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_audit_event_types(
        self,
        profile_id: str | None = None,
        limit: int = 200,
        actor: str | None = None,
    ) -> list[str]:
        clauses = ["1=1"]
        params: list[Any] = []
        if profile_id:
            clauses.append("profile_id=%s")
            params.append(profile_id)
        if actor:
            clauses.append("actor=%s")
            params.append(actor)
        where = " AND ".join(clauses)
        sql = (
            f"SELECT DISTINCT event_type FROM audit_events WHERE {where} "
            "ORDER BY event_type LIMIT %s"
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (*params, limit))
                return [str(row[0]) for row in cur.fetchall()]

    def count_platform_users(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM platform_users")
                row = cur.fetchone()
                return int(row[0]) if row else 0

    def create_platform_user(
        self, user_id: str, username: str, password_hash: str,
        role: str, display_name: str, created_at: str,
    ):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO platform_users (id, username, password_hash, role, display_name, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (user_id, username, password_hash, role, display_name, created_at),
                )

    def get_platform_user_by_username(self, username: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM platform_users WHERE username=%s", (username,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_platform_user_by_id(self, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM platform_users WHERE id=%s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def list_platform_users(self) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, username, role, display_name, created_at FROM platform_users ORDER BY username",
                )
                return [dict(r) for r in cur.fetchall()]

    def create_auth_session(self, token: str, user_id: str, expires_at: str, created_at: str):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_sessions (token, user_id, expires_at, created_at)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (token, user_id, expires_at, created_at),
                )

    def get_auth_session(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM auth_sessions WHERE token=%s", (token,))
                row = cur.fetchone()
                return dict(row) if row else None

    def delete_auth_session(self, token: str):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE token=%s", (token,))

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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_execution_approvals (
                        id, requester_user_id, requester_username, scope_type, scope_id,
                        assignment_id, profile_id, status, reason_request, context_json,
                        requested_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM live_execution_approvals WHERE id=%s",
                    (approval_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_pending_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE scope_type=%s AND scope_id=%s AND status='pending'
                    ORDER BY requested_at DESC LIMIT 1
                    """,
                    (scope_type, scope_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_approved_live_execution_approval(
        self, scope_type: str, scope_id: str,
    ) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM live_execution_approvals
                    WHERE scope_type=%s AND scope_id=%s AND status='approved'
                    ORDER BY decided_at DESC LIMIT 1
                    """,
                    (scope_type, scope_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def list_live_execution_approvals(
        self, status: str | None = None, limit: int = 100,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                if status and status != "all":
                    cur.execute(
                        """
                        SELECT * FROM live_execution_approvals
                        WHERE status=%s ORDER BY requested_at DESC LIMIT %s
                        """,
                        (status, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM live_execution_approvals
                        ORDER BY requested_at DESC LIMIT %s
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_assignment(self, assignment_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migration_assignments WHERE id=%s",
                    (assignment_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def list_assignments(self, profile_id: str) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migration_assignments WHERE profile_id=%s ORDER BY created_at",
                    (profile_id,),
                )
                return [dict(r) for r in cur.fetchall()]

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
            with conn.cursor() as cur:
                if active:
                    cur.execute(
                        "UPDATE cohort_membership SET active=0 "
                        "WHERE profile_id=%s AND ado_project=%s AND ado_repo=%s AND active=1",
                        (profile_id, ado_project, ado_repo),
                    )
                cur.execute(
                    """
                    INSERT INTO cohort_membership
                    (assignment_id, profile_id, ado_project, ado_repo, gh_org, gh_repo, active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        assignment_id, profile_id, ado_project, ado_repo,
                        gh_org, gh_repo, 1 if active else 0,
                    ),
                )

    def get_cohort_repos(self, assignment_id: str) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT ado_project, ado_repo, gh_org, gh_repo FROM cohort_membership "
                    "WHERE assignment_id=%s AND active=1",
                    (assignment_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def has_repo_in_progress(self, ado_project: str, ado_repo: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM migrations WHERE ado_project=%s AND ado_repo=%s "
                    "AND status='in_progress' LIMIT 1",
                    (ado_project, ado_repo),
                )
                row = cur.fetchone()
        return row is not None

    def is_repo_in_assignment_cohort(
        self, assignment_id: str, ado_project: str, ado_repo: str,
    ) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM cohort_membership WHERE assignment_id=%s "
                    "AND ado_project=%s AND ado_repo=%s AND active=1 LIMIT 1",
                    (assignment_id, ado_project, ado_repo),
                )
                row = cur.fetchone()
        return row is not None

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
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT status FROM live_execution_approvals WHERE id=%s",
                    (approval_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if row["status"] != "pending":
                    return self.get_live_execution_approval(approval_id)
                cur.execute(
                    """
                    UPDATE live_execution_approvals
                    SET status=%s, approver_user_id=%s, approver_username=%s,
                        reason_decision=%s, decided_at=%s
                    WHERE id=%s
                    """,
                    (
                        status, approver_user_id, approver_username,
                        reason_decision, decided_at, approval_id,
                    ),
                )
        return self.get_live_execution_approval(approval_id)
