"""Associate ADO pipelines with source repositories when ADO metadata is incomplete."""
from __future__ import annotations

import difflib
import re
from typing import Any


def normalize_repo_token(name: str) -> str:
    """Reduce a repository or pipeline name to its comparable core.

    Args:
        name: Repository or pipeline name as Azure DevOps reports it.

    Returns:
        The name in lower case with any ``migration`` decoration removed, so
        ``Payments-Migration`` and ``payments`` compare equal.
    """
    lowered = (name or "").lower().strip()
    return re.sub(r"-?migration-?", "", lowered).strip("-")


def infer_pipeline_repo_name(
    pipeline_name: str,
    repo_name: str,
    repo_id: str,
    project_repos: list[dict[str, Any]],
) -> str:
    """Resolve the Azure DevOps repository that owns a pipeline definition.

    The checks run from most to least reliable: the stored repository name, the
    repository id, an exact match on the pipeline name, a match after both
    names are normalised, a prefix match, and finally a fuzzy match.

    Args:
        pipeline_name: Name of the pipeline whose repository is wanted.
        repo_name: Repository name Azure DevOps recorded, which may be empty.
        repo_id: Repository id Azure DevOps recorded, which may be empty.
        project_repos: Repositories in the project to match against.

    Returns:
        The matching repository name, or the stored name (or an empty string)
        when nothing matches.
    """
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
    """Report whether a pipeline inventory row belongs to a repository.

    Args:
        pipeline_name: Name of the pipeline on the inventory row.
        repo_name: Repository the caller is collecting pipelines for.
        stored_repo_name: Repository name stored on the inventory row, which is
            trusted when it is set.
        project_repos: Repositories in the project, used when the association
            has to be inferred from the pipeline name.

    Returns:
        ``True`` when the pipeline should be migrated with this repository.
    """
    if stored_repo_name:
        return stored_repo_name == repo_name
    inferred = infer_pipeline_repo_name(
        pipeline_name,
        stored_repo_name,
        "",
        project_repos or [{"name": repo_name}],
    )
    return inferred == repo_name
