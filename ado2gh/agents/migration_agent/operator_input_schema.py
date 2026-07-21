"""Pydantic schema for planner/validator → operator input requests."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.intake_schema import FieldType


class OperatorInputFieldSpec(BaseModel):
    """One dynamic form field the operator must supply."""

    name: str
    label: str
    field_type: FieldType = "text"
    description: str = ""
    options: list[str] = Field(default_factory=list)
    required: bool = True


class OperatorInputRequest(BaseModel):
    """Structured request for operator decision or missing data."""

    request_id: str
    source: Literal["planner", "validator"] = "planner"
    title: str
    description: str
    blocker_keys: list[str] = Field(default_factory=list)
    fields: list[OperatorInputFieldSpec] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    def form_id(self) -> str:
        return f"operator_input_{self.request_id}"[:60]
