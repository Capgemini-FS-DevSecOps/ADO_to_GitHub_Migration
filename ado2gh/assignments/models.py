"""Dataclasses for migration assignments and cohort membership."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AssignmentType(str, Enum):
    """Cohort classification for migration assignments."""

    POC = "poc"
    PILOT = "pilot"
    WAVE = "wave"
    ADHOC = "adhoc"


@dataclass
class MigrationAssignment:
    """Named cohort binding repos to an execution phase for gate enforcement."""

    id: str
    profile_id: str
    name: str
    assignment_type: AssignmentType
    execution_phase: str
    wave_number: Optional[int] = None
    status: str = "active"
    created_by: str = ""
    repos: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "name": self.name,
            "assignment_type": self.assignment_type.value,
            "execution_phase": self.execution_phase,
            "wave_number": self.wave_number,
            "status": self.status,
            "created_by": self.created_by,
            "repos": self.repos,
        }
