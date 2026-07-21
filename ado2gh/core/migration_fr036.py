"""FR-036 helpers — one live migration per repo, stale state recovery."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ado2gh.logging_config import log

if TYPE_CHECKING:
    from ado2gh.state.db import StateDB


def other_run_holds_repo(repo_key: str, current_run_id: str | None = None) -> bool:
    """True when a different pipeline run holds the repo lock or is active for it."""
    try:
        from ado2gh.api.repo_lock import REPO_LOCK_MANAGER

        holder = REPO_LOCK_MANAGER.holder(repo_key)
        if holder and holder != current_run_id:
            return True
    except Exception:
        pass
    try:
        from ado2gh.api.pipeline_store import PipelineRunStore

        for run in PipelineRunStore.list_active_runs():
            run_id = getattr(run, "id", None)
            rid = getattr(run, "repository_id", None)
            if not rid and hasattr(run, "to_dict"):
                rid = run.to_dict().get("repository_id")
            if rid == repo_key and run_id != current_run_id:
                return True
    except Exception:
        pass
    return False


def clear_stale_in_progress_migrations(
    db: StateDB,
    ado_project: str,
    ado_repo: str,
    *,
    current_run_id: str | None = None,
) -> int:
    """Mark orphaned in_progress rows failed when no other run owns the repo."""
    repo_key = f"{ado_project}/{ado_repo}"
    if other_run_holds_repo(repo_key, current_run_id):
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
