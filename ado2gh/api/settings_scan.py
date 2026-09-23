"""Profile scan management mixin for SettingsStore."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Optional, Protocol

from ado2gh.api.phase_definitions import PhaseDefinition

if TYPE_CHECKING:
    from ado2gh.api.settings_models import MigrationProfile, UISettings

    class _ScanStoreHost(Protocol):
        """Attributes ``ScanMixin`` expects from the assembled ``SettingsStore``.

        ``load`` and ``get_phases`` are defined on ``SettingsStore`` itself;
        ``get_profile`` and ``record_scan_summary`` are defined on the sibling
        ``ProfileMixin``, reachable only once both mixins are combined on the
        host class. Both are named here since mypy checks this mixin in
        isolation.
        """

        def load(self) -> UISettings: ...

        def get_phases(self) -> list[PhaseDefinition]: ...

        def get_profile(self, profile_id: str) -> Optional[MigrationProfile]: ...

        def record_scan_summary(
            self, profile_id: str, scan: dict[str, Any],
        ) -> MigrationProfile: ...
else:
    _ScanStoreHost = object


class ScanMixin(_ScanStoreHost):
    """Async profile scan and rescan methods."""

    _rescan_lock = threading.Lock()
    _rescan_running: set[str] = set()
    _scan_jobs: dict[str, dict[str, Any]] = {}

    def profile_rescan_status(self, profile_id: str) -> dict[str, Any]:
        """Report where a profile's background scan has got to.

        Args:
            profile_id: Identifier of the profile to report on.

        Returns:
            dict[str, Any]: The profile identifier, whether a scan thread
            is running right now, the state of the most recent scan
            (idle, running, completed or failed), its error message if it
            failed, and — once it has completed — when it finished and how
            many projects, repositories and service connections it saw.

        """
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
        """Start a discovery scan of a profile's ADO organization in the background.

        Returns as soon as the worker thread is running; poll
        :meth:`profile_rescan_status` for progress. At most one scan runs
        per profile at a time.

        Args:
            profile_id: Identifier of the profile to scan.
            max_repos: Stop after this many repositories, for a quick
                sample. Defaults to scanning the whole organization.

        Returns:
            dict[str, Any]: The profile identifier and a status of
            ``started`` when a scan was launched, ``already_running`` when
            one was already in flight, or ``error`` with a reason when the
            profile does not exist or has no ADO credentials configured.

        """
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
        """Run one discovery scan to completion and persist everything it found.

        Scans the profile's ADO organization, saves the results, updates
        the profile's scan summary and copies the discovered repositories
        into the risk-score table used by migration runs. Runs on the
        calling thread.

        Args:
            profile_id: Identifier of the profile to scan.
            max_repos: Stop after this many repositories. Defaults to
                scanning the whole organization.
            phases: Phase definitions to classify repositories against.
                Defaults to the configured phases, in which case manual
                phase assignments made by operators are preserved; passing
                phases explicitly means the phases just changed, so
                assignments are recomputed from scratch.

        Returns:
            dict[str, Any]: The raw scan result — when it ran, how many
            projects and repositories were seen, the per-phase
            recommendations, the organization inventory and the resolved
            target GitHub organization.

        Raises:
            KeyError: If no profile has that identifier.
            ValueError: If the profile has no ADO credentials configured.

        """
        from ado2gh.api.migration_scan import (
            attach_pipeline_inventory,
            persist_scan_results,
            replace_scan_results,
            scan_with_credentials,
        )
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
        )
        if adv.db_path:
            attach_pipeline_inventory(
                raw,
                profile.ado_org_url,
                profile.ado_pat,
                adv.db_path,
                pipeline_parallel=int(adv.pipeline_parallel or 12),
            )
        if gh_org and not raw.get("gh_org"):
            raw["gh_org"] = gh_org
        if phases is None:
            persist_scan_results(profile_id, raw)
        else:
            replace_scan_results(profile_id, raw)
        self.record_scan_summary(profile_id, raw)
        sync_profile_scan_to_risk_scores(profile_id, config_path=adv.config_path)
        return raw

    def _start_rescan_after_phase_change(
        self,
        profile_id: str,
        phases: list[PhaseDefinition],
    ) -> dict[str, Any]:
        """Kick off a rescan in the background after the phases were edited.

        Args:
            profile_id: Identifier of the profile to rescan.
            phases: The newly saved phase definitions to reclassify the
                profile's repositories against.

        Returns:
            dict[str, Any]: The profile identifier with a status of
            ``started``, or ``already_running`` when a scan is already in
            flight; or a skipped marker and the reason when the profile
            does not exist or has no ADO credentials configured.

        """
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
        """Rescan a profile against edited phases, absorbing expected failures.

        Args:
            profile_id: Identifier of the profile to rescan.
            phases: The newly saved phase definitions to reclassify the
                profile's repositories against.

        Returns:
            dict[str, Any]: The profile identifier, how many projects and
            repositories were rescanned, when the scan finished, and a
            flag confirming the risk scores were synced; or a skipped
            marker and the reason when the profile has gone away or has no
            ADO credentials configured.

        """
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
