"""Scan diagnostics helpers."""
from __future__ import annotations

from typing import Any


def build_empty_scan_warnings(
    projects_scanned: int,
    repos_scanned: int,
    project_details: list[dict[str, Any]],
) -> list[str]:
    """Explain why an ADO organization scan returned no repositories.

    Args:
        projects_scanned: Number of ADO projects the scan enumerated.
        repos_scanned: Number of Git repositories found across those projects.
        project_details: Per-project scan results. Each entry may carry a
            ``project`` name and an ``error`` string describing why that
            project's repository listing failed.

    Returns:
        Operator-facing warning lines, most specific first: a single line when
        no projects came back at all; otherwise, when projects were found but
        no repositories were, a count of failed project listings followed by up
        to three of the underlying errors, a summary naming up to six of the
        projects scanned, and a line listing the common causes. Empty when the
        scan found repositories and needs no explanation.
    """
    warnings: list[str] = []
    if projects_scanned == 0:
        warnings.append("No ADO projects were returned for this organization.")
        return warnings

    if repos_scanned > 0:
        return warnings

    errors = [p["error"] for p in project_details if p.get("error")]
    if errors:
        warnings.append(
            f"Git repository listing failed for {len(errors)} project(s). "
            "Check that the PAT has Code (read) scope and access to each project."
        )
        for err in errors[:3]:
            warnings.append(f"  • {err}")

    names = [p.get("project", "?") for p in project_details]
    project_list = ", ".join(names[:6])
    if len(names) > 6:
        project_list += f", +{len(names) - 6} more"

    warnings.append(
        f"Scanned {projects_scanned} project(s) ({project_list}) but found 0 Git repositories."
    )
    warnings.append(
        "Common causes: projects use TFVC instead of Git, repos live in a different ADO org, "
        "or the PAT lacks Code (read) permission on those projects."
    )
    return warnings
