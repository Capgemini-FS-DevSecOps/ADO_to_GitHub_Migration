"""Risk scores, phase gates, and batch checkpoints mixin.

Extracted from SQLiteStateDB to keep file under 800 lines.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional


class RiskGatesMixin:
    """Risk scores, phase gates, batch checkpoints — mixed into SQLiteStateDB."""

    def prune_risk_scores_not_in(self, keys: set[tuple[str, str]]) -> int:
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
        if phase is None:
            with self._conn() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT * FROM repo_risk_scores ORDER BY total_score"
                ).fetchall()]
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
