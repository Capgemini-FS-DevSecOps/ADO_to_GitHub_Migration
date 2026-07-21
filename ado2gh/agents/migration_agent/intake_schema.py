"""Pydantic information schema for migration intake (orchestrator ↔ operator)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class OperatorIntent(str, Enum):
    """LLM-classified operator message intent."""

    GENERAL_CHAT = "general_chat"
    MIGRATION_INFO = "migration_info"
    MIGRATION_ACTION = "migration_action"


class IntakePhase(str, Enum):
    """Which migration step still needs operator input."""

    PLANNING = "planning"  # before invoke_planner
    PLAN_REVIEW = "plan_review"  # after planner, before execution
    CANCELLATION = "cancellation"


FieldType = Literal["text", "textarea", "select", "checkbox"]


class IntakeFieldSpec(BaseModel):
    """UI + routing metadata for one intake data point."""

    name: str
    label: str
    field_type: FieldType = "text"
    description: str = ""
    options: list[str] = Field(default_factory=list)
    required_in_phases: list[IntakePhase] = Field(default_factory=list)
    required: bool = True


class MigrationIntakeSchema(BaseModel):
    """Exact data points collected before / during migration."""

    repository_id: str | None = None
    repository_ids: list[str] | None = None
    dry_run: bool | None = None
    phase: str | None = None
    plan_confirmed: bool | None = None
    confirm_execute: bool | None = None
    plan_notes: str | None = None
    cancellation_action: Literal["stop", "rollback"] | None = None

    @field_validator("repository_id", "plan_notes", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def resolved_repository_id(self) -> str | None:
        if self.repository_id:
            return self.repository_id
        if self.repository_ids:
            return self.repository_ids[0]
        return None

    def execution_mode_label(self) -> str | None:
        if self.dry_run is None:
            return None
        return "dry-run" if self.dry_run else "live"


class OperatorMessageAnalysis(BaseModel):
    """LLM judgment of a free-text operator message (intent + intake fields)."""

    reasoning: str = Field(
        description="Step-by-step interpretation shown to the operator as agent thoughts.",
    )
    intent: OperatorIntent
    repository_id: str | None = None
    repository_ids: list[str] | None = None
    dry_run: bool | None = None
    phase: str | None = None
    plan_confirmed: bool | None = None
    confirm_execute: bool | None = None
    plan_notes: str | None = None
    cancellation_action: Literal["stop", "rollback"] | None = None
    requests_new_migration: bool = Field(
        default=False,
        description=(
            "True when the operator wants to migrate a different repository without "
            "naming it (e.g. 'another one', 'next repo', 'migrate another')."
        ),
    )
    is_cancellation: bool = Field(
        default=False,
        description="True when the operator explicitly requests cancel/stop/abort.",
    )
    repository_named_in_message: bool = Field(
        default=False,
        description=(
            "True only when the operator explicitly named a concrete repository in user_message "
            "(not inferred from session_context, known_repositories, or prior turns). "
            "False for vague migration requests, pronouns, or 'another repo' without a name."
        ),
    )

    @field_validator("repository_id", "plan_notes", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _enforce_repository_intake_policy(self) -> "OperatorMessageAnalysis":
        """Drop repository intake unless the LLM attests it was named in user_message."""
        if not self.repository_named_in_message:
            self.repository_id = None
            self.repository_ids = None
        elif self.repository_id is None and not self.repository_ids:
            self.repository_named_in_message = False
        return self

    def intake_patch(self) -> dict[str, Any]:
        """Fields to merge into MigrationIntakeSchema (excludes routing metadata)."""
        skip = {
            "reasoning",
            "intent",
            "requests_new_migration",
            "is_cancellation",
            "repository_named_in_message",
        }
        return {
            key: value
            for key, value in self.model_dump().items()
            if key not in skip and value is not None
        }


class FormIntakeSubmission(BaseModel):
    """Pydantic-validated form submission → intake schema."""

    repository_id: str | None = None
    dry_run: bool | None = None
    phase: str | None = None
    plan_confirmed: bool | None = None
    confirm_execute: bool | None = None
    plan_notes: str | None = None
    cancellation_action: Literal["stop", "rollback"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _map_form_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        if not raw.get("repository_id"):
            for alias in ("repository", "repository_name", "repo", "repo_name"):
                val = str(raw.get(alias) or "").strip()
                if val:
                    raw["repository_id"] = val
                    break
        if raw.get("dry_run") is None:
            mode = raw.get("mode") or raw.get("execution_mode")
            if mode is not None and str(mode).strip() != "":
                raw["dry_run"] = mode
        action = raw.get("action") or raw.get("cancellation_action")
        if action in ("stop", "rollback"):
            raw["cancellation_action"] = action
        return raw

    @field_validator("dry_run", mode="before")
    @classmethod
    def _coerce_dry_run(cls, value: Any) -> bool | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        mode_raw = str(value).strip().lower()
        if mode_raw in ("live", "false", "0"):
            return False
        if mode_raw in ("dry-run", "dryrun", "dry_run", "true", "1"):
            return True
        return None

    @field_validator("repository_id", "plan_notes", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def from_raw_values(cls, values: dict[str, Any]) -> FormIntakeSubmission:
        return cls.model_validate(values or {})


# Registry: schema fields → form metadata (no flow-specific form builders).
INTAKE_FIELD_REGISTRY: dict[str, IntakeFieldSpec] = {
    "repository_id": IntakeFieldSpec(
        name="repository_id",
        label="Repository",
        field_type="text",
        description="ADO repository as Project/RepoName.",
        required_in_phases=[IntakePhase.PLANNING],
    ),
    "dry_run": IntakeFieldSpec(
        name="dry_run",
        label="Dry run",
        field_type="select",
        description="true = simulate only; false = live migration.",
        options=["true", "false"],
        required_in_phases=[IntakePhase.PLANNING],
    ),
    "phase": IntakeFieldSpec(
        name="phase",
        label="Migration phase",
        field_type="select",
        description="Optional — only when the operator names a wave or phase (poc, pilot, wave1–3).",
        options=["poc", "pilot", "wave1", "wave2", "wave3"],
        required_in_phases=[],
        required=False,
    ),
    "plan_confirmed": IntakeFieldSpec(
        name="plan_confirmed",
        label="Confirm plan",
        field_type="checkbox",
        description="Plan matches operator intent.",
        required_in_phases=[IntakePhase.PLAN_REVIEW],
    ),
    "confirm_execute": IntakeFieldSpec(
        name="confirm_execute",
        label="Start after confirm",
        field_type="checkbox",
        description="Run migration immediately when plan is confirmed.",
        required_in_phases=[],
    ),
    "plan_notes": IntakeFieldSpec(
        name="plan_notes",
        label="Plan notes",
        field_type="textarea",
        description="Changes or questions about the plan.",
        required_in_phases=[],
    ),
    "cancellation_action": IntakeFieldSpec(
        name="cancellation_action",
        label="Cancellation action",
        field_type="select",
        description="Stop only or rollback session resources.",
        options=["stop", "rollback"],
        required_in_phases=[IntakePhase.CANCELLATION],
    ),
}


def required_fields_for_phase(phase: IntakePhase) -> list[str]:
    return [
        name
        for name, spec in INTAKE_FIELD_REGISTRY.items()
        if phase in spec.required_in_phases
    ]
