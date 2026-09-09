"""Concurrent-run conflict detection — one live migration per repo, stale state recovery."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ado2gh.logging_config import log

if TYPE_CHECKING:
    from ado2gh.state.db import StateDB


def get_repo_conflict_reason(repo_key: str, current_run_id: str | None = None) -> str | None:
    """Why ``repo_key`` may not be migrated now, or None when it is provably free.

    Two independent sources are asked: the process-wide repo lock table and the
    pipeline run store. A source that raises has *not* answered "free", so the
    reason string says the check was inconclusive rather than letting the caller
    read a swallowed error as "no conflict" (Principle V, fail-safe defaults).
    The returned text distinguishes a detected conflict from a failed check so an
    operator can tell the two refusals apart.

    Args:
        repo_key: Repository as `project/repo`.
        current_run_id: Pipeline run asking the question, so that a lock or an
            active run it owns itself is not read as a conflict.

    Returns:
        A sentence naming the conflicting run, or naming the source that could
        not be read, or None when both sources answered and neither reported a
        conflict.
    """
    try:
        from ado2gh.api.repo_lock import REPO_LOCK_MANAGER

        holder = REPO_LOCK_MANAGER.holder(repo_key)
        if holder and holder != current_run_id:
            return f"another pipeline run ({holder}) holds the lock on {repo_key}"
    except Exception as exc:
        return f"conflict detection failed for {repo_key}: repo lock unreadable ({exc})"
    try:
        from ado2gh.api.pipeline_store import PipelineRunStore

        for run in PipelineRunStore.list_active_runs():
            run_id = getattr(run, "id", None)
            rid = getattr(run, "repository_id", None)
            if not rid and hasattr(run, "to_dict"):
                rid = run.to_dict().get("repository_id")
            if rid == repo_key and run_id != current_run_id:
                return f"another pipeline run ({run_id}) is active for {repo_key}"
    except Exception as exc:
        return f"conflict detection failed for {repo_key}: run store unreadable ({exc})"
    return None


def other_run_holds_repo(repo_key: str, current_run_id: str | None = None) -> bool:
    """True when another run holds the repo *or* the check could not be completed.

    Args:
        repo_key: Repository as `project/repo`.
        current_run_id: Pipeline run asking the question.

    Returns:
        True when the repository is not provably free, which deliberately
        includes the case where conflict detection itself failed.
    """
    return get_repo_conflict_reason(repo_key, current_run_id) is not None


def clear_stale_in_progress_migrations(
    db: StateDB,
    ado_project: str,
    ado_repo: str,
    *,
    current_run_id: str | None = None,
) -> int:
    """Mark orphaned in_progress rows failed when no other run owns the repo.

    Args:
        db: State store holding the migration rows.
        ado_project: Azure DevOps project of the repository.
        ado_repo: Azure DevOps repository name.
        current_run_id: Pipeline run asking to clear, so its own rows are not
            mistaken for another run's.

    Returns:
        The number of rows cleared; zero when another run holds the repo, when
        conflict detection was inconclusive, or when the state backend has no
        `mark_in_progress_migrations_failed`.
    """
    repo_key = f"{ado_project}/{ado_repo}"
    reason = get_repo_conflict_reason(repo_key, current_run_id)
    if reason:
        log.warning("Not clearing in_progress rows for %s (FR-036): %s", repo_key, reason)
        return 0
    mark_failed = getattr(db, "mark_in_progress_migrations_failed", None)
    if not mark_failed:
        return 0
    cleared = mark_failed(
        ado_project,
        ado_repo,
        error="Orphaned in_progress record cleared — no other active pipeline run (FR-036)",
    )
    if cleared:
        log.warning(
            "Cleared %s orphaned in_progress migration row(s) for %s (FR-036)",
            cleared,
            repo_key,
        )
    return int(cleared or 0)
