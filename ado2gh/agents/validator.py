"""Validator — git, pipeline, access scopes; boards gaps when available."""
from __future__ import annotations

from typing import Any, List, Optional


class AgentValidator:
    """Validates migration outcomes and routes remediation."""

    def validate(
        self,
        validation_results: List[dict],
        boards_gaps: Optional[List[dict]] = None,
    ) -> dict[str, Any]:
        failures = [r for r in validation_results if r.get("overall") != "PASS"]
        boards_issues = boards_gaps or []
        passed = not failures and not boards_issues
        return {
            "passed": passed,
            "failures": failures,
            "boards_gaps": boards_issues,
            "scopes_checked": ["git", "pipelines", "access"],
        }

    def should_remediate(self, result: dict, retry_count: int, max_retries: int = 3) -> bool:
        if result.get("passed"):
            return False
        return retry_count < max_retries
