"""Azure Boards gaps report for work-item parity (FR-040–042)."""
from __future__ import annotations

from typing import Any, List


def generate_boards_gaps(
    ado_work_items: int,
    gh_issues: int,
    missing_fields: List[str] | None = None,
) -> dict[str, Any]:
    """Summarize boards migration gaps for validator and UI."""
    missing = missing_fields or []
    gap_count = max(0, ado_work_items - gh_issues) + len(missing)
    return {
        "ado_work_items": ado_work_items,
        "gh_issues": gh_issues,
        "gap_count": gap_count,
        "missing_fields": missing,
        "auto_convertible_pct": (
            round(gh_issues / ado_work_items * 100, 1) if ado_work_items else 100.0
        ),
        "gaps": [
            g for g in [
                (
                    {"type": "count_mismatch", "detail": f"ADO={ado_work_items} GH={gh_issues}"}
                    if ado_work_items != gh_issues else None
                ),
            ]
            if g
        ],
    }
