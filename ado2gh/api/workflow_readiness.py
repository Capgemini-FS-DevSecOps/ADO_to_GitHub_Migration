"""Workflow dependency readiness checks with structured log lines (FR-051)."""
from __future__ import annotations

from typing import Any, List


def check_workflow_readiness(
    repo: str,
    missing_secrets: List[str],
    missing_envs: List[str],
) -> dict:
    """Build readiness checklist and log_lines for API/UI."""
    log_lines: list[str] = []
    blockers: list[dict] = []
    for secret in missing_secrets:
        line = f"[WARNING] workflow-readiness: {repo} — MISSING secret '{secret}'"
        log_lines.append(line)
        blockers.append({"type": "secret", "name": secret, "repo": repo})
    for env in missing_envs:
        line = f"[WARNING] workflow-readiness: {repo} — MISSING environment '{env}'"
        log_lines.append(line)
        blockers.append({"type": "environment", "name": env, "repo": repo})
    if not blockers:
        log_lines.append(f"[INFO] workflow-readiness: {repo} — all critical dependencies satisfied")
    return {
        "repo": repo,
        "ready": len(blockers) == 0,
        "log_lines": log_lines,
        "blockers": blockers,
    }
