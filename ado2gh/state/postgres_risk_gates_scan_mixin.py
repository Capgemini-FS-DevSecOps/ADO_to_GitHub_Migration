"""Postgres risk scores, phase gates, batch checkpoints, and profile scan mixin.

Extracted from PostgresStateDB to keep file under 800 lines.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


class PostgresRiskGatesScanMixin:
    """Risk scores, phase gates, batch checkpoints, profile scan — for PostgresStateDB."""

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
        if phase is None:
            with self._conn() as conn:
                with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM repo_risk_scores ORDER BY total_score")
                    return [dict(r) for r in cur.fetchall()]
        from ado2gh.state.postgres_db import _phase_value
        phase_val = _phase_value(phase)
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM repo_risk_scores WHERE assigned_phase=%s ORDER BY total_score",
                    (phase_val,),
                )
                return [dict(r) for r in cur.fetchall()]

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

    def risk_score_count(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM repo_risk_scores")
                return cur.fetchone()[0]

    def save_profile_scan(
        self,
        profile_id: str,
        raw: dict[str, Any],
        *,
        preserve_manual_assignments: bool = True,
    ) -> None:
        from ado2gh.api.migration_scan import pack_scan_summary_json
        from ado2gh.api.profile_discovery import manual_phase_overrides

        now = raw.get("scanned_at") or datetime.now(timezone.utc).isoformat()
        gh_org = raw.get("gh_org", "")
        summary = pack_scan_summary_json(raw)
        overrides: dict[tuple[str, str], str] = {}
        if preserve_manual_assignments:
            overrides = manual_phase_overrides(self.get_profile_scan_repos(profile_id))
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
                for bucket in raw.get("recommendations", {}).values():
                    for repo in bucket.get("repos", []):
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
                            None,
                            None,
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

    def create_wave(
        self,
        wave_id: str,
        name: str,
        repository_ids: list[str],
        *,
        organization_id: str = "",
        description: str = "",
        created_by: str = "",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO migration_waves
                        (id, name, description, status, created_at, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (wave_id, name, description or name, "draft", now, created_by),
                )
                for order, repo_id in enumerate(repository_ids):
                    cur.execute(
                        """
                        INSERT INTO wave_repositories
                            (id, wave_id, repository_id, organization_id, migration_order, status)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT(wave_id, repository_id) DO UPDATE SET
                            migration_order=EXCLUDED.migration_order
                        """,
                        (f"{wave_id}__{repo_id}", wave_id, repo_id, organization_id, order, "pending"),
                    )
        return self.get_wave(wave_id)

    def get_wave(self, wave_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM migration_waves WHERE id=%s",
                    (wave_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    "SELECT repository_id, organization_id, migration_order, status "
                    "FROM wave_repositories WHERE wave_id=%s ORDER BY migration_order",
                    (wave_id,),
                )
                repos = cur.fetchall()
        result = dict(row)
        result["repository_ids"] = [r["repository_id"] for r in repos]
        result["repositories"] = [dict(r) for r in repos]
        return result

    def get_profile_scan_repos(
        self, profile_id: str, phase: str | None = None,
    ) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM profile_scan_repos WHERE profile_id=%s "
                    "ORDER BY total_score, project, repo_name",
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
        from ado2gh.api.migration_scan import extract_discovery_fields

        meta = self.get_profile_scan_meta(profile_id)
        if not meta:
            return None
        repos = self.get_profile_scan_repos(profile_id)
        bucket: dict[str, Any] = {
            "phase": "unassigned",
            "repo_count": 0,
            "risk_min": 0,
            "risk_max": 0,
            "rationale": "Repos are no longer automatically assigned to migration phases.",
            "repos": [],
        }
        for r in repos:
            bucket["repo_count"] += 1
            bucket["risk_min"] = min(bucket["risk_min"] or r["total_score"], r["total_score"])
            bucket["risk_max"] = max(bucket["risk_max"], r["total_score"])
            repo_data = json.loads(r.get("repo_json") or "{}")
            repo_data.pop("assigned_phase", None)
            repo_data.pop("suggested_phase", None)
            bucket["repos"].append(repo_data)
        summary_raw = meta.get("summary_json") or "{}"
        if isinstance(summary_raw, str):
            summary_raw = json.loads(summary_raw)
        discovery = extract_discovery_fields(summary_raw)
        return {
            "profile_id": profile_id,
            "scanned_at": meta["scanned_at"],
            "projects_scanned": meta["projects_scanned"],
            "repos_scanned": meta["repos_scanned"],
            "total_repos": meta["repos_scanned"],
            "gh_org": meta["gh_org"],
            "recommendations": {"unassigned": bucket},
            **discovery,
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
        from ado2gh.state.postgres_db import _phase_value
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
        from ado2gh.state.postgres_db import _phase_value
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM batch_checkpoints WHERE phase=%s ORDER BY batch_num",
                    (_phase_value(phase),),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_last_completed_batch(self, phase) -> int:
        from ado2gh.state.postgres_db import _phase_value
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(batch_num) FROM batch_checkpoints "
                    "WHERE phase=%s AND status='completed'",
                    (_phase_value(phase),),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] is not None else -1
