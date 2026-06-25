"""Profile scan management mixin for SettingsStore."""
from __future__ import annotations

import threading
from typing import Any

from ado2gh.api.phase_definitions import PhaseDefinition


class ScanMixin:
    """Async profile scan and rescan methods."""

    _rescan_lock = threading.Lock()
    _rescan_running: set[str] = set()
    _scan_jobs: dict[str, dict[str, Any]] = {}

    def profile_rescan_status(self, profile_id: str) -> dict[str, Any]:
        with self._rescan_lock:
            running = profile_id in self._rescan_running
            job = dict(self._scan_jobs.get(profile_id) or {})
        return {
            "profile_id": profile_id,
            "running": running,
            "status": job.get("status", "running" if running else "idle"),
            "error": job.get("error"),
            "scanned_at": job.get("scanned_at"),
            "repos_scanned": job.get("repos_scanned"),
            "projects_scanned": job.get("projects_scanned"),
            "service_connections": job.get("service_connections"),
        }

    def start_profile_scan(
        self,
        profile_id: str,
        *,
        max_repos: int | None = None,
    ) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if not profile:
            return {"status": "error", "profile_id": profile_id, "error": "profile not found"}
        if not profile.ado_org_url or not profile.ado_pat:
            return {
                "status": "error",
                "profile_id": profile_id,
                "error": "profile missing ADO credentials",
            }

        with self._rescan_lock:
            if profile_id in self._rescan_running:
                return {"status": "already_running", "profile_id": profile_id, "running": True}
            self._rescan_running.add(profile_id)
            self._scan_jobs[profile_id] = {
                "status": "running",
                "error": None,
                "scanned_at": None,
                "repos_scanned": None,
                "projects_scanned": None,
                "service_connections": None,
            }

        def _run() -> None:
            try:
                raw = self._execute_profile_scan(profile_id, max_repos=max_repos)
                org = raw.get("org_inventory") or {}
                self._scan_jobs[profile_id] = {
                    "status": "completed",
                    "error": None,
                    "scanned_at": raw.get("scanned_at"),
                    "repos_scanned": raw.get("repos_scanned", 0),
                    "projects_scanned": raw.get("projects_scanned", 0),
                    "service_connections": org.get("total_service_connections", 0),
                }
            except Exception as exc:
                self._scan_jobs[profile_id] = {
                    "status": "failed",
                    "error": str(exc),
                    "scanned_at": None,
                    "repos_scanned": None,
                    "projects_scanned": None,
                    "service_connections": None,
                }
            finally:
                with self._rescan_lock:
                    self._rescan_running.discard(profile_id)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"profile-scan-{profile_id}",
        ).start()
        return {"status": "started", "profile_id": profile_id, "running": True}

    def _execute_profile_scan(
        self,
        profile_id: str,
        *,
        max_repos: int | None = None,
        phases: list[PhaseDefinition] | None = None,
    ) -> dict[str, Any]:
        from ado2gh.api.migration_scan import persist_scan_results, scan_with_credentials
        from ado2gh.api.profile_discovery import resolve_gh_org, sync_profile_scan_to_risk_scores

        profile = self.get_profile(profile_id)
        if not profile:
            raise KeyError(profile_id)
        if not profile.ado_org_url or not profile.ado_pat:
            raise ValueError("profile missing ADO credentials")

        adv = self.load().advanced
        gh_org = resolve_gh_org(profile, config_path=adv.config_path)
        phase_defs = phases or self.get_phases()
        raw = scan_with_credentials(
            profile.ado_org_url,
            profile.ado_pat,
            gh_org=gh_org,
            max_repos=max_repos,
            phase_definitions=[p.to_dict() for p in phase_defs],
            db_path=adv.db_path,
            run_inventory=True,
            pipeline_parallel=int(adv.pipeline_parallel or 12),
        )
        if gh_org and not raw.get("gh_org"):
            raw["gh_org"] = gh_org
        preserve = phases is None
        persist_scan_results(
            profile_id,
            raw,
            preserve_manual_assignments=preserve,
        )
        self.record_scan_summary(profile_id, raw)
        sync_profile_scan_to_risk_scores(profile_id, config_path=adv.config_path)
        return raw

    def _start_rescan_after_phase_change(
        self,
        profile_id: str,
        phases: list[PhaseDefinition],
    ) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if not profile:
            return {"skipped": True, "reason": "profile not found"}
        if not profile.ado_org_url or not profile.ado_pat:
            return {"skipped": True, "reason": "profile missing ADO credentials"}

        with self._rescan_lock:
            if profile_id in self._rescan_running:
                return {"status": "already_running", "profile_id": profile_id}
            self._rescan_running.add(profile_id)

        def _run() -> None:
            try:
                self._rescan_profile_after_phase_change(profile_id, phases)
            finally:
                with self._rescan_lock:
                    self._rescan_running.discard(profile_id)

        threading.Thread(target=_run, daemon=True, name=f"phase-rescan-{profile_id}").start()
        return {"status": "started", "profile_id": profile_id}

    def _rescan_profile_after_phase_change(
        self,
        profile_id: str,
        phases: list[PhaseDefinition],
    ) -> dict[str, Any]:
        try:
            raw = self._execute_profile_scan(profile_id, phases=phases)
        except KeyError:
            return {"skipped": True, "reason": "profile not found"}
        except ValueError as exc:
            return {"skipped": True, "reason": str(exc)}
        return {
            "profile_id": profile_id,
            "repos_scanned": raw.get("repos_scanned", 0),
            "projects_scanned": raw.get("projects_scanned", 0),
            "scanned_at": raw.get("scanned_at", ""),
            "synced": True,
        }
