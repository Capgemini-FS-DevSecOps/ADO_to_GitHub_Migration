"""Resolve natural-language cohort references to assignment repo sets."""
from __future__ import annotations

import re

from ado2gh.assignments.models import AssignmentType, MigrationAssignment
from ado2gh.assignments.store import AssignmentStore


class AssignmentResolver:
    """Map POC/pilot/wave names to persisted assignments (FR-030)."""

    WAVE_RE = re.compile(r"wave\s*(\d+)", re.I)

    def __init__(self, store: AssignmentStore):
        self.store = store

    def resolve(self, profile_id: str, text: str) -> MigrationAssignment | None:
        text_l = text.lower()
        assignments = self.store.list_for_profile(profile_id)
        if "poc" in text_l:
            return self._by_type(assignments, AssignmentType.POC)
        if "pilot" in text_l:
            return self._by_type(assignments, AssignmentType.PILOT)
        m = self.WAVE_RE.search(text)
        if m:
            num = int(m.group(1))
            for a in assignments:
                if a.assignment_type == AssignmentType.WAVE and a.wave_number == num:
                    return a
        for a in assignments:
            if a.name.lower() in text_l:
                return a
        return None

    def _by_type(self, assignments, atype: AssignmentType):
        for a in assignments:
            if a.assignment_type == atype:
                return a
        return None
