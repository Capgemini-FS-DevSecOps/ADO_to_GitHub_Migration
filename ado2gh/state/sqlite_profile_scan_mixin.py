"""Profile scan mixin — discovery scan persistence per migration profile.

Extracted from SQLiteStateDB to keep file under 800 lines.
Provides: save_profile_scan, get_profile_scan_meta, get_profile_scan_repos,
update_profile_repo_phases, build_profile_scan_payload.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


class ProfileScanMixin:
    """Profile scan and wave methods — mixed into SQLiteStateDB / PostgresStateDB."""

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
            for bucket in raw.get("recommendations", {}).values():
                bucket_phase = bucket.get("phase", "")
                for repo in bucket.get("repos", []):
                    project = repo.get("project", "")
                    repo_name = repo.get("repo_name", "")
                    suggested = repo.get("suggested_phase") or bucket_phase
                    assigned = repo.get("assigned_phase") or bucket_phase
                    if preserve_manual_assignments and (project, repo_name) in overrides:
                        assigned = overrides[(project, repo_name)]
                    conn.execute("""
                        INSERT INTO profile_scan_repos
                            (profile_id, project, repo_name, total_score, suggested_phase,
                             assigned_phase, gh_org, gh_repo, pipeline_count, repo_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        profile_id,
                        project,
                        repo_name,
                        repo.get("total_score", 0),
                        suggested,
                        assigned,
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
            rows = conn.execute(
                "SELECT * FROM profile_scan_repos WHERE profile_id=? "
                "ORDER BY total_score, project, repo_name",
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
        from ado2gh.api.migration_scan import extract_discovery_fields

        meta = self.get_profile_scan_meta(profile_id)
        if not meta:
            return None
        repos = self.get_profile_scan_repos(profile_id)
        buckets: dict[str, dict[str, Any]] = {}
        for r in repos:
            phase = r.get("assigned_phase") or r.get("suggested_phase") or "unassigned"
            if phase not in buckets:
                buckets[phase] = {
                    "phase": phase,
                    "repo_count": 0,
                    "risk_min": 0,
                    "risk_max": 0,
                    "rationale": "",
                    "repos": [],
                }
            bucket = buckets[phase]
            bucket["repo_count"] += 1
            bucket["risk_min"] = min(bucket["risk_min"] or r["total_score"], r["total_score"])
            bucket["risk_max"] = max(bucket["risk_max"], r["total_score"])
            repo_data = json.loads(r.get("repo_json") or "{}")
            repo_data["assigned_phase"] = r.get("assigned_phase")
            repo_data["suggested_phase"] = r.get("suggested_phase")
            bucket["repos"].append(repo_data)
        summary_raw = json.loads(meta.get("summary_json") or "{}")
        discovery = extract_discovery_fields(summary_raw)
        return {
            "profile_id": profile_id,
            "scanned_at": meta["scanned_at"],
            "projects_scanned": meta["projects_scanned"],
            "repos_scanned": meta["repos_scanned"],
            "total_repos": meta["repos_scanned"],
            "gh_org": meta["gh_org"],
            "recommendations": buckets,
            **discovery,
        }
