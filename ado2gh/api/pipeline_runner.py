"""Accelerator pipeline runner  multi-step manual migration workflow.

Decomposed into:
  - pipeline_models: data models, step definitions, enrichment
  - pipeline_store: in-memory run registry
  - pipeline_steps: step implementation mixin
  - pipeline_runner: core runner orchestration (this file)

Re-exports all public names for backward compatibility (FR-013).
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from typing import Callable

from ado2gh.api.pipeline_models import (
    ACCELERATOR_PIPELINE_STEPS,
    AGENT_MIGRATION_PIPELINE_STEPS,
    MIGRATE_UI_PIPELINE_STEPS,
    PipelineRun,
    PipelineStep,
    StepStatus,
    enrich_pipeline_run_dict,
    resolve_pipeline_step_defs,
)
from ado2gh.api.pipeline_steps import PipelineStepsMixin
from ado2gh.api.pipeline_store import PipelineRunStore
from ado2gh.api.repo_lock import REPO_LOCK_MANAGER
from ado2gh.api.settings_store import SettingsStore
from ado2gh.api.step_prerequisites import StepPrerequisiteChecker

__all__ = [
    "ACCELERATOR_PIPELINE_STEPS",
    "AGENT_MIGRATION_PIPELINE_STEPS",
    "MIGRATE_UI_PIPELINE_STEPS",
    "PipelineRun",
    "PipelineRunStore",
    "PipelineRunner",
    "PipelineStep",
    "StepStatus",
    "enrich_pipeline_run_dict",
    "resolve_pipeline_step_defs",
]


class PipelineRunner(PipelineStepsMixin):
    def __init__(self, settings: SettingsStore | None = None):
        self.settings = settings or SettingsStore()
        self._prereq_checker = StepPrerequisiteChecker()

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
                elif status in (StepStatus.COMPLETED, StepStatus.WARN, StepStatus.FAILED, StepStatus.SKIPPED):
                    s.completed_at = now
                break
        run.updated_at = datetime.now(timezone.utc).isoformat()

    def _execute(self, run_id: str, step_ids: list[str] | None) -> None:
        run = PipelineRunStore.get(run_id)
        if not run:
            return
        run.status = "running"
        self.settings.apply_to_process_env()

        targets = step_ids or [s.id for s in run.steps]
        handlers: dict[str, Callable[[PipelineRun], None]] = {
            "connect": self._step_connect,
            "analyze_deps": self._step_analyze_deps,
            "discover": self._step_discover,
            "inventory": self._step_inventory,
            "readiness": self._step_readiness,
            "assign": self._step_assign,
            "migrate": lambda r: self._migrate_scoped(r, "migrate", None),
            "migrate_repos": lambda r: self._migrate_scoped(r, "migrate_repos", ["repo"]),
            "convert_pipelines": lambda r: self._migrate_scoped(
                r, "convert_pipelines", ["pipelines"],
            ),
            "convert_metadata": lambda r: self._migrate_scoped(
                r, "convert_metadata",
                ["branch_policies", "wiki", "work_items"],
            ),
            "validate": self._step_validate,
        }

        try:
            for step_id in targets:
                if self._cancelled(run.id):
                    self._stop_remaining_steps(run)
                    return
                step = next((s for s in run.steps if s.id == step_id), None)
                if not step or step.status in (StepStatus.COMPLETED, StepStatus.WARN):
                    continue
                ok, missing = self._prereq_checker.check(run, step_id)
                if not ok:
                    block_msg = self._prereq_checker.failure_message(missing, step.label)
                    self._set_step(run, step_id, StepStatus.FAILED, block_msg)
                    run.status = "failed"
                    run.error = block_msg
                    self._log(run, f"BLOCKED {step.label}: {block_msg}")
                    return
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
                if run.dry_run:
                    run.status = "dry_run_complete"
                    self._log(run, "Dry-run pipeline finished (no migrations recorded)")
                else:
                    run.status = "completed"
                    self._log(run, "Pipeline completed successfully")
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            self._log(run, f"ERROR: {exc}\n{traceback.format_exc()}")
        finally:
            REPO_LOCK_MANAGER.release_all(run.id)
            PipelineRunStore.clear_cancel(run.id)
            if run.status in ("cancelled", "failed", "completed", "dry_run_complete"):
                try:
                    from ado2gh.agents.migration_agent.session.lifecycle import (
                        clear_pipeline_run_migration_state,
                    )

                    clear_pipeline_run_migration_state(run)
                except Exception:
                    pass
