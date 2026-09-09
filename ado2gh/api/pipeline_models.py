"""Pipeline data models, step definitions, and enrichment helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ado2gh.state.factory import StateStore


class StepStatus(str, Enum):
    """Lifecycle status of a single pipeline step.

    A ``str`` enum so members compare equal to the plain strings that reach the
    API and the console, and so they serialise without conversion.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    WARN = "warn"
    FAILED = "failed"
    SKIPPED = "skipped"


ACCELERATOR_PIPELINE_STEPS: list[dict[str, str]] = [
    {"id": "connect", "label": "Connect & validate credentials",
     "description": "Verify ADO PAT, GitHub token, and org access"},
    {"id": "discover", "label": "Discover repositories",
     "description": "Scan ADO projects and repos; sync profile discovery"},
    {"id": "inventory", "label": "Inventory ADO pipelines",
     "description": "Deep-scan YAML, classic, and release pipeline definitions into StateDB"},
    {"id": "readiness", "label": "Assess conversion readiness",
     "description": "Classify auto/assisted/manual; flag blockers (Key Vault, self-hosted agents, secrets)"},
    {"id": "assign", "label": "Assign migration phases",
     "description": "Risk-score repos and assign to poc/pilot/wave phases"},
    {"id": "analyze_deps", "label": "Analyze dependencies",
     "description": "Consolidated dependency analysis: service connections, variable groups, repo-to-repo, environments, self-hosted agents, and task inputs. Reports warnings as a paginated bulleted list (merged former Map Secrets step)"},
    {"id": "migrate_repos", "label": "Migrate repository contents",
     "description": "Feasibility analysis (size, LFS, branches, tags) then git mirror, GEI transfer, or manual. Continue-on-error for batches; per-repo lock"},
    {"id": "convert_pipelines", "label": "Convert pipelines → GitHub Actions",
     "description": "Transform ADO YAML, validate via actionlint or YAML fallback, auto-commit workflows in live mode with versioned conflict handling"},
    {"id": "convert_metadata", "label": "Convert branch policies & wiki",
     "description": "Branch protection rules, wiki pages, and work items → GitHub"},
    {"id": "migrate", "label": "Run all scoped migrations",
     "description": "Execute every enabled scope for repos in the selected phase"},
    {"id": "validate", "label": "Validate migrated repos",
     "description": "Commit SHA / branch parity AND committed workflow integrity verification"},
]

MIGRATE_UI_PIPELINE_STEPS: list[dict[str, str]] = [
    {"id": "connect", "label": "Load discovery data",
     "description": "Load profile discovery data for the selected repos"},
    {"id": "analyze_deps", "label": "Analyze dependencies",
     "description": "Resolve all dependencies (service connections, variable groups, repo-to-repo, environments) and determine migration order (merged secrets/SC analysis)"},
    {"id": "migrate_repos", "label": "Migrate repositories",
     "description": "Transfer git content to GitHub (mirror or GEI)"},
    {"id": "convert_pipelines", "label": "Convert workflows",
     "description": "ADO pipelines → GitHub Actions YAML with validation and auto-commit"},
    {"id": "validate", "label": "Validate",
     "description": "Commit SHA verification and workflow integrity check"},
]

AGENT_MIGRATION_PIPELINE_STEPS: list[dict[str, str]] = MIGRATE_UI_PIPELINE_STEPS

_PIPELINE_STEP_INDEX: dict[str, dict[str, str]] = {
    s["id"]: s for s in ACCELERATOR_PIPELINE_STEPS
}


def resolve_pipeline_step_defs(step_ids: list[str] | None) -> list[dict[str, str]]:
    """Resolve step metadata for a requested step id list (preserves order)."""
    if not step_ids:
        return ACCELERATOR_PIPELINE_STEPS
    resolved: list[dict[str, str]] = []
    for step_id in step_ids:
        if step_id in _PIPELINE_STEP_INDEX:
            resolved.append(_PIPELINE_STEP_INDEX[step_id])
        else:
            resolved.append({
                "id": step_id,
                "label": step_id.replace("_", " ").title(),
                "description": "",
            })
    return resolved


@dataclass
class PipelineStep:
    """One step of a pipeline run, with its status and outcome.

    Carries the step's identity and console-facing description alongside the
    mutable execution record: current status, the last message shown against it,
    start and completion timestamps, and a free-form result dictionary that
    later steps read (for example the dependency map and migration order that
    ``analyze_deps`` leaves behind).
    """

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
    """A single execution of the migration pipeline.

    Holds what the run targets (phase, wave, optional single repository and
    whether its dependencies come with it), whether it is a dry run, who started
    it and who approved live execution, the ordered steps with their individual
    results, and the accumulated log lines the console tails.

    Mutated in place while the run executes: the registry hands out the live
    object, so a caller that holds a run sees its progress.
    """

    id: str
    name: str
    status: str = "pending"
    dry_run: bool = True
    phase: str = ""
    wave_id: Optional[int] = None
    repository_id: Optional[str] = None
    migrate_deps_only: bool = True
    # Free operator text. Redacted at the point it is persisted (pipeline_steps.py),
    # and deliberately absent from to_dict() so it never rides back out in a response.
    override_reason: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    started_by_user_id: Optional[str] = None
    started_by_username: Optional[str] = None
    started_by_display_name: Optional[str] = None
    approved_by_username: Optional[str] = None
    approved_by_display_name: Optional[str] = None
    live_approval_status: Optional[str] = None

    def current_step_label(self) -> str:
        """Return the label of the step the run is on, for the progress banner.

        Prefers the step that is actually running; when none is, falls back to
        the first step still pending, so a run between steps still shows where
        it is headed.

        Returns:
            str: The running step's label, else the next pending step's label,
            else an empty string once every step has finished or been skipped.
        """
        for step in self.steps:
            if step.status == "running":
                return step.label
        for step in self.steps:
            if step.status == "pending":
                return step.label
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise the run for the API and the console.

        Deliberately omits ``override_reason``: the operator's free-text
        escalation justification is persisted in redacted form and must never
        ride back out in a response.

        Returns:
            dict[str, Any]: The run's identity and status fields, its
            configuration (``dry_run``, ``phase``, ``wave_id``,
            ``repository_id``, ``migrate_deps_only``), every step as a plain
            dictionary, the most recent 500 log lines, the operator who started
            it and — when applicable — the approver and live approval status,
            plus a ``current_step`` label for the progress banner.
        """
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "dry_run": self.dry_run,
            "phase": self.phase,
            "wave_id": self.wave_id,
            "repository_id": self.repository_id,
            "migrate_deps_only": self.migrate_deps_only,
            "steps": [asdict(s) for s in self.steps],
            "logs": self.logs[-500:],
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_by_user_id": self.started_by_user_id,
            "started_by_username": self.started_by_username,
            "started_by_display_name": self.started_by_display_name,
            "approved_by_username": self.approved_by_username,
            "approved_by_display_name": self.approved_by_display_name,
            "live_approval_status": self.live_approval_status,
            "current_step": self.current_step_label(),
        }


def enrich_pipeline_run_dict(
    run_dict: dict[str, Any], db: "StateStore | None" = None,
) -> dict[str, Any]:
    """Attach display-friendly started and approval labels to a serialised run.

    Resolves the run's live-execution approval state, preferring the value
    already on the run and falling back to the persisted approval row when a
    database is supplied. A dry run needs no approval, a run awaiting one is
    pending, and anything else that reached execution was approved implicitly by
    the operator's own authority.

    Args:
        run_dict: A run as produced by :meth:`PipelineRun.to_dict`. Mutated in
            place and also returned.
        db: State database used to look up a persisted live-execution approval
            for this run. When omitted, the approval state is inferred from the
            run alone.

    Returns:
        dict[str, Any]: The same dictionary, with ``started_by_label`` naming
        the operator who started the run, ``live_approval_status`` set to one of
        ``not_required``, ``pending``, ``approved``, ``denied`` or
        ``auto_approved``, and ``approved_by_label`` carrying the matching
        human-readable phrase. Approver names are also filled in from the
        persisted row when they were not already present.
    """
    started = (
        run_dict.get("started_by_display_name")
        or run_dict.get("started_by_username")
        or "Unknown"
    )
    run_dict["started_by_label"] = started

    status = run_dict.get("live_approval_status")
    if not status and db and hasattr(db, "get_live_execution_approval_for_scope"):
        row = db.get_live_execution_approval_for_scope("pipeline_run", run_dict.get("id", ""))
        if row:
            raw = str(row.get("status") or "")
            if raw == "approved":
                status = "approved"
                run_dict.setdefault("approved_by_username", row.get("approver_username"))
                run_dict.setdefault(
                    "approved_by_display_name",
                    row.get("approver_username"),
                )
            elif raw == "pending":
                status = "pending"
            elif raw == "denied":
                status = "denied"
                run_dict.setdefault("approved_by_username", row.get("approver_username"))
                run_dict.setdefault(
                    "approved_by_display_name",
                    row.get("approver_username"),
                )

    if not status:
        if run_dict.get("dry_run"):
            status = "not_required"
        elif run_dict.get("status") == "awaiting_approval":
            status = "pending"
        else:
            status = "auto_approved"

    run_dict["live_approval_status"] = status

    if status == "not_required":
        run_dict["approved_by_label"] = "Not required (dry run)"
    elif status == "auto_approved":
        run_dict["approved_by_label"] = "Auto-approved"
    elif status == "pending":
        run_dict["approved_by_label"] = "Awaiting approval"
    elif status == "denied":
        approver = (
            run_dict.get("approved_by_display_name")
            or run_dict.get("approved_by_username")
            or "approver"
        )
        run_dict["approved_by_label"] = f"Denied by {approver}"
    else:
        approver = (
            run_dict.get("approved_by_display_name")
            or run_dict.get("approved_by_username")
            or "Unknown"
        )
        run_dict["approved_by_label"] = approver

    return run_dict
