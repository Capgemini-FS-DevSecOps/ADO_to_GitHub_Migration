"""Step prerequisite checking for flexible pipeline execution order.

Implements the flexible-ordering contract for the migration pipeline: each step
declares a list of prerequisite step IDs that must have finished acceptably
(``completed`` or ``warn``) before the step may execute. Steps can therefore be
run in any order as long as their prerequisites are satisfied within the run.

A prerequisite that is *absent* from the current run's pipeline (i.e. the run was
created without that step) is considered out-of-scope and is not enforced — this
keeps the lighter ``MIGRATE_UI_PIPELINE_STEPS`` flow working without the deeper
``ACCELERATOR_PIPELINE_STEPS`` steps. A prerequisite that *is* part of the run but
has not completed will block the dependent step (per FR-009).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ado2gh.api.pipeline_runner import PipelineRun

# Step statuses that satisfy a prerequisite (the step finished acceptably).
# StepStatus is a ``str`` Enum, so equality against these literals holds for
# both enum members and plain strings.
SATISFIED_STATUSES: tuple[str, ...] = ("completed", "warn", "skipped")

# Maps each step ID to the list of step IDs that must finish before it runs.
STEP_PREREQUISITES: dict[str, list[str]] = {
    "connect": [],
    "discover": ["connect"],
    "inventory": ["connect"],
    "readiness": ["inventory"],
    "assign": ["discover"],
    "analyze_deps": ["inventory"],
    "migrate_repos": ["analyze_deps"],
    "convert_pipelines": ["analyze_deps"],
    "convert_metadata": ["migrate_repos"],
    "migrate": ["analyze_deps"],
    "validate": ["migrate_repos", "convert_pipelines"],
}


class StepPrerequisiteChecker:
    """Validates that a step's prerequisites are satisfied within a run."""

    def __init__(self, prerequisites: dict[str, list[str]] | None = None) -> None:
        """Bind the checker to a prerequisite map.

        Args:
            prerequisites: Maps each step id to the ids that must finish before
                it may run. Defaults to :data:`STEP_PREREQUISITES`; pass an
                explicit map (including an empty one, which disables all
                checks) to override it.
        """
        self.prerequisites = prerequisites if prerequisites is not None else STEP_PREREQUISITES

    def check(self, run: "PipelineRun", step_id: str) -> tuple[bool, list[str]]:
        """Return ``(ok, missing_labels)`` for ``step_id`` against ``run``.

        ``ok`` is ``True`` when every in-scope prerequisite has completed/warned.
        ``missing_labels`` contains the human-readable labels of prerequisites
        that are present in the run but have not finished acceptably.
        """
        required = self.prerequisites.get(step_id, [])
        if not required:
            return True, []

        steps_by_id = {s.id: s for s in run.steps}
        missing: list[str] = []
        for prereq_id in required:
            prereq_step = steps_by_id.get(prereq_id)
            if prereq_step is None:
                # Prerequisite is not part of this run's pipeline — out of scope.
                continue
            status = getattr(prereq_step, "status", "")
            if status not in SATISFIED_STATUSES:
                missing.append(self._label(prereq_id, run))
        return (not missing), missing

    @staticmethod
    def _label(step_id: str, run: "PipelineRun") -> str:
        """Resolve a human-readable label for a step id.

        Prefers the label carried by the step on this run, falls back to the
        canonical accelerator step index, and finally derives one from the id
        itself, so a message is always readable even for an unknown step.

        Args:
            step_id: Identifier of the step to label.
            run: Run whose own steps are consulted first.

        Returns:
            str: The step's display label, or a title-cased form of the id when
            the step is unknown to both the run and the step index.
        """
        for s in run.steps:
            if s.id == step_id:
                return s.label
        # _PIPELINE_STEP_INDEX lives in pipeline_models, not pipeline_runner
        # (which never imports or re-exports it); the old import path here
        # would raise ImportError the moment this fallback branch ran.
        from ado2gh.api.pipeline_models import _PIPELINE_STEP_INDEX

        meta = _PIPELINE_STEP_INDEX.get(step_id)
        if meta:
            return meta["label"]
        return step_id.replace("_", " ").title()

    def failure_message(self, missing_labels: list[str], step_label: str) -> str:
        """Build the message shown when a step is blocked by its prerequisites.

        Args:
            missing_labels: Display labels of the prerequisites that have not
                finished acceptably, as returned by :meth:`check`.
            step_label: Display label of the step that is being blocked.

        Returns:
            str: A single sentence naming the outstanding prerequisites and the
            step they block, suitable for the run log and the step's failure
            message.
        """
        prereqs = ", ".join(missing_labels)
        return f"{prereqs} must be completed before {step_label} can proceed."
