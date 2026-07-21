"""Typed contracts shared by the pipeline Planner-Executor-Validator flow.

These types deliberately live outside :mod:`ado2gh.models` so that the PEV
implementation can evolve without changing the persisted inventory schema.
Only JSON-safe summaries of these objects are returned to callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ConversionMode(str, Enum):
    """How a conversion plan is expected to be executed."""

    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    MANUAL = "manual"


class FindingSeverity(str, Enum):
    """Stable severity vocabulary used in evidence artifacts and gates."""

    INFO = "info"
    WARNING = "warning"
    MANUAL_REVIEW = "manual_review"
    ERROR = "error"


@dataclass(frozen=True)
class PlanAmbiguity:
    """One source construct that deterministic rules cannot safely resolve."""

    ambiguity_id: str
    kind: str
    location: tuple[Any, ...]
    rationale: str
    source: Any = None
    llm_eligible: bool = False
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ambiguity_id": self.ambiguity_id,
            "kind": self.kind,
            "location": list(self.location),
            "rationale": self.rationale,
            "source": self.source,
            "llm_eligible": self.llm_eligible,
            "required": self.required,
        }


@dataclass(frozen=True)
class ConversionPlan:
    """Immutable, deterministic plan for converting a single pipeline."""

    plan_id: str
    source_fingerprint: str
    ruleset_version: str
    pipeline_id: int
    pipeline_name: str
    mode: ConversionMode
    deterministic_steps: int
    expected_executable_steps: int
    ambiguities: tuple[PlanAmbiguity, ...] = ()
    notices: tuple[str, ...] = ()

    @property
    def llm_ambiguities(self) -> tuple[PlanAmbiguity, ...]:
        return tuple(a for a in self.ambiguities if a.llm_eligible)

    @property
    def manual_ambiguities(self) -> tuple[PlanAmbiguity, ...]:
        return tuple(a for a in self.ambiguities if not a.llm_eligible)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "source_fingerprint": self.source_fingerprint,
            "ruleset_version": self.ruleset_version,
            "pipeline_id": self.pipeline_id,
            "pipeline_name": self.pipeline_name,
            "mode": self.mode.value,
            "deterministic_steps": self.deterministic_steps,
            "expected_executable_steps": self.expected_executable_steps,
            "ambiguities": [a.to_dict() for a in self.ambiguities],
            "notices": list(self.notices),
        }


@dataclass(frozen=True)
class LLMResolution:
    """A schema-checked LLM proposal for one ambiguity."""

    ambiguity_id: str
    replacement: Any
    confidence: float
    rationale: str = ""
    model: str = ""
    prompt_digest: str = ""
    response_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        # Rationale is intentionally retained only in the local evidence file;
        # callers may choose to persist just the digest and confidence.
        return {
            "ambiguity_id": self.ambiguity_id,
            "replacement": self.replacement,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "model": self.model,
            "prompt_digest": self.prompt_digest,
            "response_digest": self.response_digest,
        }


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: FindingSeverity
    message: str
    location: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "location": self.location,
        }


@dataclass
class ValidationReport:
    """Deterministic validation result for a generated workflow."""

    findings: list[ValidationFinding] = field(default_factory=list)
    workflow_digest: str = ""
    parsed_workflow: Optional[dict[str, Any]] = field(default=None, repr=False)

    @property
    def valid(self) -> bool:
        return not any(f.severity == FindingSeverity.ERROR for f in self.findings)

    @property
    def production_ready(self) -> bool:
        blocking = {FindingSeverity.ERROR, FindingSeverity.MANUAL_REVIEW}
        return not any(f.severity in blocking for f in self.findings)

    @property
    def status(self) -> str:
        if not self.valid:
            return "failed"
        if not self.production_ready:
            return "needs_review"
        return "passed"

    def add(
        self,
        code: str,
        severity: FindingSeverity,
        message: str,
        location: str = "",
    ) -> None:
        self.findings.append(ValidationFinding(code, severity, message, location))

    def to_dict(self) -> dict[str, Any]:
        counts = {severity.value: 0 for severity in FindingSeverity}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return {
            "status": self.status,
            "valid": self.valid,
            "production_ready": self.production_ready,
            "workflow_digest": self.workflow_digest,
            "counts": counts,
            "findings": [f.to_dict() for f in self.findings],
        }


class PipelineConversionError(RuntimeError):
    """Base error for PEV conversion failures."""


class PipelineValidationError(PipelineConversionError):
    """Raised when configured validation gates reject a workflow."""

    def __init__(
        self,
        message: str,
        report: ValidationReport,
        *,
        evidence_file: str = "",
        result: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.report = report
        self.evidence_file = evidence_file
        self.result = result or {}
