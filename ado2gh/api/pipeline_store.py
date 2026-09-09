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
        """Return the cancellation flag for a run, creating it on first use.

        Args:
            run_id: Identifier of the run whose cancellation flag is wanted.

        Returns:
            threading.Event: The event shared by the API thread that requests
            cancellation and the worker thread that polls it between steps. The
            same event is returned for every call with the same ``run_id`` until
            :meth:`clear_cancel` discards it.
        """
        with cls._lock:
            if run_id not in cls._cancel_events:
                cls._cancel_events[run_id] = threading.Event()
            return cls._cancel_events[run_id]

    @classmethod
    def request_cancel(cls, run_id: str) -> bool:
        """Ask an in-flight run to stop at its next step boundary.

        Sets the run's cancellation flag and marks it ``cancelled`` immediately,
        so the console reflects the request without waiting for the worker
        thread to notice.

        Args:
            run_id: Identifier of the run to cancel.

        Returns:
            bool: ``True`` when the run existed and was still pending or
            running, so cancellation was requested; ``False`` when the run is
            unknown or has already finished, in which case nothing changed.
        """
        run = cls.get(run_id)
        if not run or run.status not in ("pending", "running"):
            return False
        cls.cancel_event(run_id).set()
        run.status = "cancelled"
        run.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    @classmethod
    def clear_cancel(cls, run_id: str) -> None:
        """Discard a finished run's cancellation flag.

        Called once a run reaches a terminal state so the registry does not
        accumulate one event per run for the life of the process.

        Args:
            run_id: Identifier of the run whose flag should be released. Unknown
                ids are ignored.
        """
        with cls._lock:
            cls._cancel_events.pop(run_id, None)

    @classmethod
    def list_runs(cls, limit: int = 20, offset: int = 0) -> tuple[list[PipelineRun], int]:
        """Return one page of runs, newest first.

        Args:
            limit: Maximum number of runs on the page. Values below one are
                treated as one.
            offset: Number of runs to skip from the newest end. Negative values
                are treated as zero.

        Returns:
            tuple[list[PipelineRun], int]: The runs on the requested page,
            ordered by creation time descending, together with the total number
            of runs held by the registry (not the page length), so the caller
            can render pagination.
        """
        with cls._lock:
            runs = sorted(cls._runs.values(), key=lambda r: r.created_at, reverse=True)
            total = len(runs)
            start = max(0, offset)
            end = start + max(1, limit)
            return runs[start:end], total

    @classmethod
    def list_active_runs(cls) -> list[PipelineRun]:
        """Return every run that has not yet reached a terminal state.

        Returns:
            list[PipelineRun]: Runs whose status is ``pending``, ``running`` or
            ``awaiting_approval``, newest first. Used by the dashboard to show
            migrations currently in flight.
        """
        active_statuses = {"pending", "running", "awaiting_approval"}
        with cls._lock:
            runs = [
                r for r in cls._runs.values()
                if r.status in active_statuses
            ]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    @classmethod
    def summary(cls) -> dict[str, int]:
        """Count the runs in the registry by outcome, for the dashboard tiles.

        Returns:
            dict[str, int]: Counts keyed by ``total`` (every run), ``dry_run``
            (runs flagged as dry runs or that finished a dry run),
            ``completed_live`` (runs that completed with dry run off),
            ``active`` (pending or running), ``awaiting_approval`` and
            ``failed``. The buckets overlap by design — a run can be counted in
            both ``dry_run`` and ``active``.
        """
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
        """Look up a single run by id.

        Args:
            run_id: Identifier of the run to fetch.

        Returns:
            Optional[PipelineRun]: The live run object — mutating it updates the
            registry — or ``None`` when no run with that id exists in this
            process.
        """
        return cls._runs.get(run_id)

    @classmethod
    def create(  # noqa: PLR0913 — see exception-register.md: no existing model holds this column set
        cls,
        name: str,
        *,
        dry_run: bool,
        phase: str,
        wave_id: int | None,
        step_defs: list[dict[str, str]] | None = None,
        started_by_user_id: str | None = None,
        started_by_username: str | None = None,
        started_by_display_name: str | None = None,
        repository_id: str | None = None,
        migrate_deps_only: bool = True,
        override_reason: str = "",
    ) -> PipelineRun:
        """Register a new pipeline run and return it ready to execute.

        Assigns a fresh UUID, stamps creation and update times, and expands the
        step definitions into pending :class:`PipelineStep` objects. The run is
        added to the registry before it is returned, so it is immediately
        visible to :meth:`get` and :meth:`list_runs`.

        Args:
            name: Operator-facing label for the run.
            dry_run: Whether the run should simulate rather than perform the
                migration. Stays a boolean here because it is a wire field.
            phase: Migration phase whose repos the run targets.
            wave_id: Wave to run, or ``None`` to derive one from the phase.
            step_defs: Step definitions to expand into run steps. Defaults to
                the full accelerator pipeline.
            started_by_user_id: Platform user id of the operator who started the
                run, when the request was authenticated.
            started_by_username: Username of the operator who started the run.
            started_by_display_name: Display name of the operator who started
                the run, used in preference to the username in the console.
            repository_id: ``"project/repo"`` of a single repo to migrate, or
                ``None`` to migrate every repo in the phase.
            migrate_deps_only: For a single-repo run, whether to also migrate
                the repos it depends on rather than that repo alone.
            override_reason: Escalation justification recorded when the operator
                overrides a gate. Redacted before it is persisted.

        Returns:
            PipelineRun: The newly registered run, with all steps pending and no
            live approval decision recorded yet.
        """
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
            override_reason=override_reason,
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
