"""Accelerator pipeline runner — multi-step manual migration workflow."""
from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from ado2gh.api.settings_store import SettingsStore


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


ACCELERATOR_PIPELINE_STEPS: list[dict[str, str]] = [
    {"id": "connect", "label": "Connect & Validate", "description": "Verify ADO and GitHub credentials"},
    {"id": "discover", "label": "Discover", "description": "Scan ADO org for repos and pipelines"},
    {"id": "inventory", "label": "Pipeline Inventory", "description": "Deep-scan pipeline definitions"},
    {"id": "readiness", "label": "Readiness", "description": "Classify pipelines auto/assisted/manual"},
    {"id": "assign", "label": "Phase Assign", "description": "Risk-score repos and assign phases"},
    {"id": "migrate", "label": "Migration Run", "description": "Execute migration wave or phase"},
    {"id": "validate", "label": "Validate", "description": "Commit SHA verification"},
]

MIGRATE_UI_PIPELINE_STEPS: list[dict[str, str]] = [
    {"id": "connect", "label": "Connect & Validate", "description": "Verify credentials and load profile discovery"},
    {"id": "migrate", "label": "Migration Run", "description": "Execute migration for repos in the selected phase"},
    {"id": "validate", "label": "Validate", "description": "Commit SHA verification against source"},
]


@dataclass
class PipelineStep:
    id: str
    label: str
    description: str
    status: StepStatus = StepStatus.PENDING
    message: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineRun:
    id: str
    name: str
    status: str = "pending"
    dry_run: bool = True
    phase: str = "poc"
    wave_id: Optional[int] = None
    steps: list[PipelineStep] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "dry_run": self.dry_run,
            "phase": self.phase,
            "wave_id": self.wave_id,
            "steps": [asdict(s) for s in self.steps],
            "logs": self.logs[-500:],
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PipelineRunStore:
    """In-memory run registry (MVP — survives API process lifetime only)."""

    _runs: dict[str, PipelineRun] = {}
    _lock = threading.Lock()
    _cancel_events: dict[str, threading.Event] = {}

    @classmethod
    def cancel_event(cls, run_id: str) -> threading.Event:
        with cls._lock:
            if run_id not in cls._cancel_events:
                cls._cancel_events[run_id] = threading.Event()
            return cls._cancel_events[run_id]

    @classmethod
    def request_cancel(cls, run_id: str) -> bool:
        run = cls.get(run_id)
        if not run or run.status not in ("pending", "running"):
            return False
        cls.cancel_event(run_id).set()
        run.status = "cancelled"
        run.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    @classmethod
    def clear_cancel(cls, run_id: str) -> None:
        with cls._lock:
            cls._cancel_events.pop(run_id, None)

    @classmethod
    def list_runs(cls, limit: int = 50) -> list[PipelineRun]:
        with cls._lock:
            runs = sorted(cls._runs.values(), key=lambda r: r.created_at, reverse=True)
            return runs[:limit]

    @classmethod
    def get(cls, run_id: str) -> Optional[PipelineRun]:
        return cls._runs.get(run_id)

    @classmethod
    def create(
        cls,
        name: str,
        dry_run: bool,
        phase: str,
        wave_id: int | None,
        step_defs: list[dict[str, str]] | None = None,
    ) -> PipelineRun:
        defs = step_defs or ACCELERATOR_PIPELINE_STEPS
        now = datetime.now(timezone.utc).isoformat()
        steps = [
            PipelineStep(
                id=s["id"],
                label=s["label"],
                description=s["description"],
            )
            for s in defs
        ]
        run = PipelineRun(
            id=str(uuid.uuid4()),
            name=name,
            dry_run=dry_run,
            phase=phase,
            wave_id=wave_id,
            steps=steps,
            created_at=now,
            updated_at=now,
        )
        with cls._lock:
            cls._runs[run.id] = run
        return run


class PipelineRunner:
    def __init__(self, settings: SettingsStore | None = None):
        self.settings = settings or SettingsStore()

    def cancel(self, run_id: str) -> bool:
        return PipelineRunStore.request_cancel(run_id)

    def start_async(self, run_id: str, step_ids: list[str] | None = None) -> None:
        PipelineRunStore.cancel_event(run_id).clear()
        t = threading.Thread(target=self._execute, args=(run_id, step_ids), daemon=True)
        t.start()

    def _cancelled(self, run_id: str) -> bool:
        return PipelineRunStore.cancel_event(run_id).is_set()

    def _stop_remaining_steps(self, run: PipelineRun) -> None:
        for step in run.steps:
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                self._set_step(run, step.id, StepStatus.SKIPPED, "Cancelled by user")
        run.status = "cancelled"
        self._log(run, "Run cancelled by user")

    def _log(self, run: PipelineRun, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        run.logs.append(f"[{ts}] {msg}")
        run.updated_at = datetime.now(timezone.utc).isoformat()

    def _set_step(self, run: PipelineRun, step_id: str, status: StepStatus,
                  message: str = "", result: dict | None = None) -> None:
        for s in run.steps:
            if s.id == step_id:
                s.status = status
                s.message = message
                if result:
                    s.result = result
                now = datetime.now(timezone.utc).isoformat()
                if status == StepStatus.RUNNING:
                    s.started_at = now
                elif status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED):
                    s.completed_at = now
                break
        run.updated_at = datetime.now(timezone.utc).isoformat()

    def _execute(self, run_id: str, step_ids: list[str] | None) -> None:
        run = PipelineRunStore.get(run_id)
        if not run:
            return
        run.status = "running"
        adv = self.settings.load().advanced
        self.settings.apply_to_process_env()

        targets = step_ids or [s.id for s in run.steps]
        handlers: dict[str, Callable[[PipelineRun], None]] = {
            "connect": self._step_connect,
            "discover": self._step_discover,
            "inventory": self._step_inventory,
            "readiness": self._step_readiness,
            "assign": self._step_assign,
            "migrate": self._step_migrate,
            "validate": self._step_validate,
        }

        try:
            for step_id in targets:
                if self._cancelled(run.id):
                    self._stop_remaining_steps(run)
                    return
                step = next((s for s in run.steps if s.id == step_id), None)
                if not step or step.status == StepStatus.COMPLETED:
                    continue
                self._set_step(run, step_id, StepStatus.RUNNING)
                self._log(run, f"Starting: {step.label}")
                try:
                    handlers[step_id](run)
                except Exception as exc:
                    self._set_step(run, step_id, StepStatus.FAILED, str(exc))
                    run.status = "failed"
                    run.error = str(exc)
                    self._log(run, f"FAILED {step.label}: {exc}")
                    return
                step = next(s for s in run.steps if s.id == step_id)
                if step.status == StepStatus.FAILED:
                    run.status = "failed"
                    return
                if self._cancelled(run.id):
                    self._stop_remaining_steps(run)
                    return
            if run.status != "cancelled":
                run.status = "completed"
                self._log(run, "Pipeline completed successfully")
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            self._log(run, f"ERROR: {exc}\n{traceback.format_exc()}")
        finally:
            PipelineRunStore.clear_cancel(run.id)

    def _step_connect(self, run: PipelineRun) -> None:
        from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
        from ado2gh.api.profile_discovery import (
            ensure_profile_scan,
            require_gh_org,
            resolve_gh_org,
            sync_profile_scan_to_risk_scores,
        )
        from ado2gh.core.config_loader import ConfigLoader

        adv = self.settings.load().advanced
        global_cfg, _ = ConfigLoader.load(adv.config_path)
        profile = self.settings.get_active_profile()
        gh_org = resolve_gh_org(profile, global_cfg=global_cfg)
        if profile and gh_org:
            global_cfg = {**global_cfg, "gh_org": gh_org}

        ado = _build_ado_client(global_cfg)
        gh = _build_gh_client(global_cfg)
        projects = ado.list_projects()
        gh.token_manager.check_rate_limits()
        require_gh_org(profile, global_cfg=global_cfg)

        discovery_msg = ""
        if profile:
            from ado2gh.api.migration_scan import load_scan_results
            from ado2gh.api.profile_discovery import repo_configs_for_phase

            cached = load_scan_results(profile.id)
            if cached and cached.get("repos_scanned", 0) > 0:
                self._log(
                    run,
                    f"Using cached discovery scan ({cached.get('repos_scanned', 0)} repos, "
                    f"scanned {cached.get('scanned_at', 'unknown')})",
                )
            else:
                self._log(run, "No cached scan — running read-only ADO discovery scan")
            scan = ensure_profile_scan(profile, self.settings)
            synced = sync_profile_scan_to_risk_scores(
                profile.id, scan, db_path=adv.db_path, config_path=adv.config_path,
            )
            phase_repos = repo_configs_for_phase(
                run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
            )
            discovery_msg = (
                f"; GitHub target: {gh_org}; Profile discovery: {scan.get('repos_scanned', len(phase_repos))} repos "
                f"({synced} synced); phase {run.phase}: {len(phase_repos)} queued"
            )
            for repo in phase_repos:
                self._log(
                    run,
                    f"  queued {repo.ado_project}/{repo.ado_repo} -> "
                    f"{repo.gh_org}/{repo.gh_repo} [scopes={','.join(repo.scopes or ['repo'])}]",
                )
        else:
            discovery_msg = "; No active profile — using migration.yaml only"

        self._set_step(
            run, "connect", StepStatus.COMPLETED,
            f"ADO: {len(projects)} projects; GitHub token OK{discovery_msg}",
            {"projects": len(projects), "profile_id": profile.id if profile else None},
        )

    def _step_discover(self, run: PipelineRun) -> None:
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.api.contracts import DiscoverRequest

        adv = self.settings.load().advanced
        accel = Accelerator(db_path=adv.db_path)
        result = accel.discover(DiscoverRequest(config_path=adv.config_path))
        self._set_step(run, "discover", StepStatus.COMPLETED,
                       f"Discovery written to {result.output_dir}",
                       {"output_dir": result.output_dir})

    def _step_inventory(self, run: PipelineRun) -> None:
        from ado2gh.api.accelerator import Accelerator
        from ado2gh.clients.ado_client import ADOClient
        from ado2gh.clients.ado_token_manager import ADOTokenManager
        from ado2gh.core.config_loader import ConfigLoader
        import os

        adv = self.settings.load().advanced
        global_cfg, _ = ConfigLoader.load(adv.config_path)
        ado_url = os.environ.get("ADO_ORG_URL") or global_cfg.get("ado_org_url", "")
        ado_pat = os.environ.get("ADO_PAT") or global_cfg.get("ado_pat", "")
        ado = ADOClient(ado_url, token_manager=ADOTokenManager.from_single(ado_pat))
        projects = [p["name"] for p in ado.list_projects()]
        accel = Accelerator(db_path=adv.db_path)
        stats = accel.inventory(adv.config_path, projects=projects)
        self._set_step(run, "inventory", StepStatus.COMPLETED,
                       f"Inventory: {stats.get('pipelines', 0)} pipelines",
                       stats)

    def _step_readiness(self, run: PipelineRun) -> None:
        from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport
        from ado2gh.state.factory import create_state_db

        adv = self.settings.load().advanced
        report = PipelineReadinessReport(create_state_db(adv.db_path)).generate()
        self._set_step(run, "readiness", StepStatus.COMPLETED,
                       f"Auto: {report.get('auto', 0)} | Assisted: {report.get('assisted', 0)} | Manual: {report.get('manual', 0)}",
                       {"auto": report.get("auto"), "assisted": report.get("assisted"), "manual": report.get("manual")})

    def _step_assign(self, run: PipelineRun) -> None:
        from pathlib import Path
        from ado2gh.core.config_loader import ConfigLoader

        adv = self.settings.load().advanced
        phase_path = Path(adv.config_path.replace(".yaml", "_phase.yaml"))
        if phase_path.exists():
            self._set_step(run, "assign", StepStatus.COMPLETED,
                           f"Using existing {phase_path.name}",
                           {"output": str(phase_path)})
            return
        _, waves = ConfigLoader.load(adv.config_path)
        if waves:
            self._set_step(run, "assign", StepStatus.COMPLETED,
                           f"Using {len(waves)} wave(s) from {adv.config_path}",
                           {"waves": len(waves)})
        else:
            self._set_step(run, "assign", StepStatus.SKIPPED,
                           "No waves in config — run ado2gh phase assign or edit migration.yaml",
                           {})

    def _step_migrate(self, run: PipelineRun) -> None:
        from ado2gh.api.accelerator import Accelerator, _build_ado_client, _build_gh_client
        from ado2gh.api.contracts import PhaseRunRequest, RunWaveRequest
        from ado2gh.api.profile_discovery import build_wave_from_profile_phase, require_gh_org
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.core.migration_engine import MigrationEngine
        from ado2gh.phase.batch_executor import BatchExecutor
        from ado2gh.phase.progress_tracker import ProgressTracker
        from ado2gh.state.factory import create_state_db

        adv = self.settings.load().advanced
        profile = self.settings.get_active_profile()
        cancel_event = PipelineRunStore.cancel_event(run.id)

        if profile:
            gh_org = require_gh_org(profile, config_path=adv.config_path)
            wave = build_wave_from_profile_phase(
                run.phase,
                profile,
                wave_id=run.wave_id or 9000,
                db_path=adv.db_path,
                parallel=adv.repo_parallel,
                pipeline_parallel=adv.pipeline_parallel,
                config_path=adv.config_path,
            )
            if wave and wave.repos:
                mode = "DRY RUN (no GitHub writes)" if run.dry_run else "LIVE"
                self._log(
                    run,
                    f"Migrating {len(wave.repos)} repo(s) for phase {run.phase} [{mode}] → {gh_org}",
                )
                global_cfg, _ = ConfigLoader.load(adv.config_path)
                global_cfg = {**global_cfg, "gh_org": gh_org}
                ado = _build_ado_client(global_cfg)
                gh = _build_gh_client(global_cfg)
                db = create_state_db(adv.db_path)
                engine = MigrationEngine(global_cfg, ado, gh, db, dry_run=run.dry_run)
                executor = BatchExecutor(
                    engine, db,
                    ProgressTracker(total_repos=len(wave.repos), total_pipelines=1),
                )

                from ado2gh.api.run_reporting import migrate_repo_detail

                def on_repo_done(key: str, res: dict, repo_cfg) -> None:
                    detail = migrate_repo_detail(key, res)
                    self._log(run, detail["summary"])

                result = executor.execute_wave(
                    wave,
                    dry_run=run.dry_run,
                    cancel_event=cancel_event,
                    on_repo_done=on_repo_done,
                )
                repo_details = [
                    migrate_repo_detail(k, v) for k, v in result.get("repos", {}).items()
                ]
                result["repo_details"] = repo_details
                if cancel_event.is_set():
                    self._set_step(run, "migrate", StepStatus.SKIPPED,
                                   "Migration cancelled by user", result)
                    return
                failed_names = [d["repo"] for d in repo_details if d["status"] != "completed"]
                msg = f"Phase {run.phase}: {result['completed']} completed, {result['failed']} failed"
                if failed_names:
                    shown = ", ".join(failed_names[:5])
                    extra = f" (+{len(failed_names) - 5} more)" if len(failed_names) > 5 else ""
                    msg += f" — failed: {shown}{extra}"
                self._set_step(run, "migrate", StepStatus.COMPLETED, msg, result)
                return

        accel = Accelerator(db_path=adv.db_path)
        if run.wave_id is not None:
            result = accel.run_wave(RunWaveRequest(
                config_path=adv.config_path,
                wave_id=run.wave_id,
                dry_run=run.dry_run,
                db_path=adv.db_path,
            ))
            msg = f"Wave {result.wave_id}: {result.completed}/{result.total} completed"
            self._set_step(run, "migrate", StepStatus.COMPLETED, msg, result.__dict__)
        else:
            phase_cfg = adv.config_path.replace(".yaml", "_phase.yaml")
            config_for_phase = phase_cfg if _phase_config_exists(adv.config_path) else adv.config_path
            result = accel.run_phase(PhaseRunRequest(
                config_path=config_for_phase,
                phase=run.phase,
                dry_run=run.dry_run,
                force=True,
                db_path=adv.db_path,
            ))
            if result.completed == 0 and result.failed == 0:
                self._set_step(
                    run, "migrate", StepStatus.SKIPPED,
                    f"No repos assigned to phase {run.phase} — assign phases on Discovery tab",
                    result.__dict__,
                )
                return
            msg = f"Phase {result.phase}: {result.completed} completed, {result.failed} failed"
            self._set_step(run, "migrate", StepStatus.COMPLETED, msg, result.__dict__)

    def _step_validate(self, run: PipelineRun) -> None:
        from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
        from ado2gh.api.profile_discovery import repo_configs_for_phase
        from ado2gh.api.run_reporting import validation_message, validation_repo_detail
        from ado2gh.core.config_loader import ConfigLoader
        from ado2gh.reporting.post_migration_validator import PostMigrationValidator
        from ado2gh.state.factory import create_state_db

        if run.dry_run:
            self._log(
                run,
                "Skipping validation — dry run did not push code to GitHub "
                "(disable dry run on Migrate to validate after a real migration)",
            )
            self._set_step(
                run, "validate", StepStatus.SKIPPED,
                "Skipped — dry run (no GitHub target to verify)",
                {"total": 0, "passed": 0, "failed": 0, "skipped_reason": "dry_run"},
            )
            return

        adv = self.settings.load().advanced
        profile = self.settings.get_active_profile()
        global_cfg, waves = ConfigLoader.load(adv.config_path)
        gh_org = resolve_gh_org(profile, global_cfg=global_cfg, config_path=adv.config_path)
        if gh_org:
            global_cfg = {**global_cfg, "gh_org": gh_org}

        repos = []
        if profile:
            repos = repo_configs_for_phase(
                run.phase, profile, db_path=adv.db_path, config_path=adv.config_path,
            )
        if not repos:
            from ado2gh.cli.helpers import load_repos
            repos = load_repos(None, global_cfg, waves)

        migrate_step = next((s for s in run.steps if s.id == "migrate"), None)
        if migrate_step and migrate_step.result:
            from ado2gh.api.run_reporting import migrate_repo_detail

            repo_details = migrate_step.result.get("repo_details") or []
            if not repo_details:
                statuses = migrate_step.result.get("repos") or {}
                repo_details = [
                    migrate_repo_detail(k, v) for k, v in statuses.items()
                ]
            ok_keys = {d["repo"] for d in repo_details if d["status"] == "completed"}
            skipped = []
            filtered = []
            for repo in repos:
                key = f"{repo.ado_project}/{repo.ado_repo}"
                if key in ok_keys or not repo_details:
                    filtered.append(repo)
                else:
                    skipped.append(key)
            for key in skipped:
                self._log(run, f"  skip validate {key} — migration did not complete")
            repos = filtered

        if not repos:
            self._set_step(run, "validate", StepStatus.SKIPPED,
                           "No successfully migrated repos to validate",
                           {"total": 0, "passed": 0, "failed": 0})
            return

        self._log(run, f"Validating {len(repos)} repo(s) (read-only ADO + GitHub API checks)")
        ado = _build_ado_client(global_cfg)
        gh = _build_gh_client(global_cfg)
        db = create_state_db(adv.db_path)
        validator = PostMigrationValidator(ado, gh, db)
        results = validator.validate(repos, output_path=adv.config_path.replace(".yaml", "_validation.csv"))
        repo_details = [validation_repo_detail(r) for r in results]
        for detail in repo_details:
            key = f"{detail['project']}/{detail['repo']}"
            self._log(
                run,
                f"  {key} -> {detail['gh_target']}: {detail['overall']} — {detail['primary_reason']}",
            )
            for check in detail.get("checks", []):
                if check.get("verdict") in ("FAIL", "WARN"):
                    self._log(
                        run,
                        f"    {check['check']}: {check['verdict']} — {check.get('detail', '')}",
                    )

        passed = sum(1 for r in results if r.get("overall") == "PASS")
        failed_rows = [r for r in results if r.get("overall") == "FAIL"]
        failed = len(failed_rows)
        status = StepStatus.COMPLETED if failed == 0 else StepStatus.FAILED
        msg = validation_message(results)
        self._set_step(
            run, "validate", status, msg,
            {
                "passed": passed,
                "failed": failed,
                "total": len(results),
                "repo_details": repo_details,
            },
        )
        if failed:
            reasons = [
                f"{d['project']}/{d['repo']}: {d['primary_reason']}"
                for d in repo_details if d["overall"] == "FAIL"
            ]
            raise RuntimeError(
                f"Validation failed for {failed} repo(s):\n" + "\n".join(reasons[:15])
                + (f"\n... and {len(reasons) - 15} more" if len(reasons) > 15 else "")
            )


def _phase_config_exists(config_path: str) -> bool:
    from pathlib import Path
    return Path(config_path.replace(".yaml", "_phase.yaml")).exists()
