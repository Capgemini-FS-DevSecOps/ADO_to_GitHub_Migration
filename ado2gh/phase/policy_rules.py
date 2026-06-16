"""Profile policy rules evaluated after base phase gates (FR-019)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class PolicyRules:
    """Structured v1 policy settings per migration profile."""

    bulk_wave_max_repos: Optional[int] = None
    production_requires_extra_approver: bool = False
    cleanup_requires_approver: bool = True
    rollback_requires_approver: bool = True
    program_order_phases: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> PolicyRules:
        if not data:
            return cls()
        return cls(
            bulk_wave_max_repos=data.get("bulk_wave_max_repos"),
            production_requires_extra_approver=bool(
                data.get("production_requires_extra_approver", False)
            ),
            cleanup_requires_approver=bool(data.get("cleanup_requires_approver", True)),
            rollback_requires_approver=bool(data.get("rollback_requires_approver", True)),
            program_order_phases=list(data.get("program_order_phases", [])),
        )


class PolicyEvaluator:
    """Evaluate supplemental policy blocks after PhaseGateChecker."""

    def __init__(self, rules: PolicyRules):
        self.rules = rules

    def check_live_migration(
        self,
        repo_count: int,
        execution_phase: str,
        passed_phases: set[str],
    ) -> List[str]:
        """Return failure reasons; empty list means policy pass."""
        failures: list[str] = []
        if self.rules.bulk_wave_max_repos and repo_count > self.rules.bulk_wave_max_repos:
            failures.append(
                f"repo_count={repo_count} > bulk_wave_max={self.rules.bulk_wave_max_repos}"
            )
        if self.rules.production_requires_extra_approver and execution_phase.lower() in (
            "production",
            "prod",
        ):
            failures.append("production_phase_requires_extra_approver")
        for req in self.rules.program_order_phases:
            if req not in passed_phases and execution_phase != req:
                failures.append(f"program_order: phase {req} not passed")
        return failures
