"""Scan diagnostics helpers."""
from __future__ import annotations

from typing import Any


def build_empty_scan_warnings(
    projects_scanned: int,
    repos_scanned: int,
    project_details: list[dict[str, Any]],
) -> list[str]:
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
