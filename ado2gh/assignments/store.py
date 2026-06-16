"""CRUD for migration assignments and cohort membership."""
from __future__ import annotations

import json
from uuid import uuid4

from ado2gh.assignments.models import AssignmentType, MigrationAssignment


class AssignmentStore:
    """Persistence for assignments against StateDB agentic tables."""

    def __init__(self, db):
        self.db = db

    def create(
        self,
        profile_id: str,
        name: str,
        assignment_type: AssignmentType,
        execution_phase: str,
        repos: list[dict],
        wave_number: int | None = None,
        created_by: str = "",
    ) -> MigrationAssignment:
        """Create assignment and active cohort memberships."""
        aid = f"asgn_{uuid4().hex[:10]}"
        self.db.insert_assignment(
            id=aid,
            profile_id=profile_id,
            name=name,
            assignment_type=assignment_type.value,
            execution_phase=execution_phase,
            wave_number=wave_number,
            status="active",
            created_by=created_by,
        )
        for r in repos:
            self.db.upsert_cohort_membership(
                assignment_id=aid,
                profile_id=profile_id,
                ado_project=r["ado_project"],
                ado_repo=r["ado_repo"],
                gh_org=r.get("gh_org", ""),
                gh_repo=r.get("gh_repo", ""),
                active=True,
            )
        return self.get(aid)

    def get(self, assignment_id: str) -> MigrationAssignment | None:
        row = self.db.get_assignment(assignment_id)
        if not row:
            return None
        repos = self.db.get_cohort_repos(assignment_id)
        return MigrationAssignment(
            id=row["id"],
            profile_id=row["profile_id"],
            name=row["name"],
            assignment_type=AssignmentType(row["assignment_type"]),
            execution_phase=row["execution_phase"],
            wave_number=row.get("wave_number"),
            status=row["status"],
            created_by=row.get("created_by", ""),
            repos=repos,
        )

    def list_for_profile(self, profile_id: str) -> list[MigrationAssignment]:
        rows = self.db.list_assignments(profile_id)
        return [self.get(r["id"]) for r in rows if self.get(r["id"])]

    def get_execution_phase(self, assignment_id: str) -> str | None:
        """Resolve linked execution phase for gate checks (FR-034)."""
        row = self.db.get_assignment(assignment_id)
        return row["execution_phase"] if row else None

    def repo_keys_for_assignment(self, assignment_id: str) -> list[str]:
        repos = self.db.get_cohort_repos(assignment_id)
        return [f"{r['ado_project']}/{r['ado_repo']}" for r in repos]
