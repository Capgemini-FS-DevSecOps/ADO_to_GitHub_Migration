"""Pipeline data models, step definitions, and enrichment helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
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
    phase: str = ""
    wave_id: Optional[int] = None
    repository_id: Optional[str] = None
    migrate_deps_only: bool = True
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
        for step in self.steps:
            if step.status == "running":
                return step.label
        for step in self.steps:
            if step.status == "pending":
                return step.label
        return ""

    def to_dict(self) -> dict[str, Any]:
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


def enrich_pipeline_run_dict(run_dict: dict[str, Any], db: Any | None = None) -> dict[str, Any]:
    """Attach display-friendly started/approved labels for the migration monitor."""
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
