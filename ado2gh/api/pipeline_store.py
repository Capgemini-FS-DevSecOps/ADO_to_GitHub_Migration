"""In-memory pipeline run registry."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from ado2gh.api.pipeline_models import (
    ACCELERATOR_PIPELINE_STEPS,
    PipelineRun,
    PipelineStep,
)


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
    def list_runs(cls, limit: int = 20, offset: int = 0) -> tuple[list[PipelineRun], int]:
        with cls._lock:
            runs = sorted(cls._runs.values(), key=lambda r: r.created_at, reverse=True)
            total = len(runs)
            start = max(0, offset)
            end = start + max(1, limit)
            return runs[start:end], total

    @classmethod
    def list_active_runs(cls) -> list[PipelineRun]:
        active_statuses = {"pending", "running", "awaiting_approval"}
        with cls._lock:
            runs = [
                r for r in cls._runs.values()
                if r.status in active_statuses
            ]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    @classmethod
    def summary(cls) -> dict[str, int]:
        with cls._lock:
            runs = list(cls._runs.values())
        return {
            "total": len(runs),
            "completed_live": sum(
                1 for r in runs if r.status == "completed" and not r.dry_run
            ),
            "dry_run": sum(
                1 for r in runs if r.dry_run or r.status == "dry_run_complete"
            ),
            "active": sum(1 for r in runs if r.status in ("running", "pending")),
            "awaiting_approval": sum(1 for r in runs if r.status == "awaiting_approval"),
            "failed": sum(1 for r in runs if r.status == "failed"),
        }

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
        *,
        started_by_user_id: str | None = None,
        started_by_username: str | None = None,
        started_by_display_name: str | None = None,
        repository_id: str | None = None,
        migrate_deps_only: bool = True,
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
            repository_id=repository_id,
            migrate_deps_only=migrate_deps_only,
            steps=steps,
            created_at=now,
            updated_at=now,
            started_by_user_id=started_by_user_id,
            started_by_username=started_by_username,
            started_by_display_name=started_by_display_name,
            live_approval_status=None,
        )
        with cls._lock:
            cls._runs[run.id] = run
        return run
