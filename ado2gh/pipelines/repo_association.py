"""Associate ADO pipelines with source repositories when ADO metadata is incomplete."""
from __future__ import annotations

import difflib
import re
from typing import Any


def normalize_repo_token(name: str) -> str:
    lowered = (name or "").lower().strip()
    return re.sub(r"-?migration-?", "", lowered).strip("-")


def infer_pipeline_repo_name(
    pipeline_name: str,
    repo_name: str,
    repo_id: str,
    project_repos: list[dict[str, Any]],
) -> str:
    """Resolve the ADO git repo that owns a pipeline definition."""
    repo_names = [r.get("name", "") for r in project_repos if r.get("name")]
    if not repo_names:
        return repo_name or pipeline_name

    by_id = {str(r.get("id", "")): r.get("name", "") for r in project_repos if r.get("id")}

    if repo_name and repo_name in repo_names:
        return repo_name
    if repo_id and str(repo_id) in by_id:
        return by_id[str(repo_id)]

    if pipeline_name in repo_names:
        return pipeline_name

    pipe_norm = normalize_repo_token(pipeline_name)
    for candidate in repo_names:
        if normalize_repo_token(candidate) == pipe_norm:
            return candidate

    pipe_lower = pipeline_name.lower()
    for candidate in sorted(repo_names, key=len, reverse=True):
        cand_lower = candidate.lower()
        if pipe_lower == cand_lower:
            return candidate
        if pipe_lower.startswith(f"{cand_lower}-") or pipe_lower.startswith(f"{cand_lower}_"):
            return candidate

    fuzzy = difflib.get_close_matches(pipeline_name, repo_names, n=1, cutoff=0.72)
    if fuzzy:
        return fuzzy[0]

    return repo_name or ""


def pipeline_belongs_to_repo(
    pipeline_name: str,
    repo_name: str,
    stored_repo_name: str,
    project_repos: list[dict[str, Any]] | None = None,
) -> bool:
    """True when a pipeline inventory row should be treated as part of *repo_name*."""
    if stored_repo_name:
        return stored_repo_name == repo_name
    inferred = infer_pipeline_repo_name(
        pipeline_name,
        stored_repo_name,
        "",
        project_repos or [{"name": repo_name}],
    )
    return inferred == repo_name
