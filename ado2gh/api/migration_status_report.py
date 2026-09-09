"""Migration progress report from StateDB and recent pipeline runs."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ado2gh.state.factory import StateStore


def _collect_repo_scope_rows(db: StateStore) -> dict[str, dict[str, Any]]:
    """Reduce the migration table to the latest row per repo and scope.

    Args:
        db: State store to read the migration rows from.

    Returns:
        One entry per ``"<project>/<repo>"`` carrying the ADO and GitHub
        names, a ``scopes`` map of scope to its latest status, completion
        time and error, and an ``errors`` list of the failure messages.
    """
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in db.get_all_migrations():
        key = (row["ado_project"], row["ado_repo"], row["scope"])
        prev = latest.get(key)
        if not prev or int(row.get("id", 0)) > int(prev.get("id", 0)):
            latest[key] = row

    repos: dict[str, dict[str, Any]] = {}
    for item in latest.values():
        key = f"{item['ado_project']}/{item['ado_repo']}"
        entry = repos.setdefault(
            key,
            {
                "ado_repo": key,
                "gh_repo": f"{item.get('gh_org', '')}/{item.get('gh_repo', '')}".strip("/"),
                "scopes": {},
                "errors": [],
            },
        )
        scope = item.get("scope", "")
        status = item.get("status", "unknown")
        entry["scopes"][scope] = {
            "status": status,
            "completed_at": item.get("completed_at"),
            "error": item.get("error_message"),
        }
        if status == "failed" and item.get("error_message"):
            entry["errors"].append(str(item["error_message"]))
    return repos


def _rollup_repo_status(scopes: dict[str, dict]) -> str:
    """Reduce a repo's per-scope statuses to one overall status.

    Args:
        scopes: The ``scopes`` map from :func:`_collect_repo_scope_rows`.

    Returns:
        ``failed`` if any scope failed, ``completed`` when the git scope and
        every other scope completed, ``partial`` when the git scope completed
        but others have not or when only some scopes completed,
        ``in_progress`` when a scope is still running, and ``not_started``
        otherwise.
    """
    if not scopes:
        return "not_started"
    statuses = {s.get("status") for s in scopes.values()}
    if "failed" in statuses:
        return "failed"
    git = scopes.get("repo") or scopes.get("git")
    if git and git.get("status") == "completed":
        if all(s.get("status") == "completed" for s in scopes.values()):
            return "completed"
        return "partial"
    if any(s.get("status") == "completed" for s in scopes.values()):
        return "partial"
    if any(s.get("status") in ("in_progress", "running") for s in scopes.values()):
        return "in_progress"
    return "not_started"


def extract_outcomes_from_pipeline_runs(runs: list[Any]) -> list[dict[str, Any]]:
    """Flatten pipeline runs into one row per repo touched by a migration step.

    Only the ``migrate_repos``, ``convert_pipelines`` and ``validate`` steps
    are considered; both the ``repo_details`` and the ``work_items`` shapes of
    a step result are read.

    Args:
        runs: Pipeline runs, either run objects exposing ``to_dict()`` or the
            already-serialised dicts.

    Returns:
        One row per repo outcome carrying the run id, name, status, dry-run
        flag and phase, the step id, the repo, its status, a summary line and
        the list of errors.
    """
    outcomes: list[dict[str, Any]] = []
    for run in runs:
        run_dict = run.to_dict() if hasattr(run, "to_dict") else run
        for step in run_dict.get("steps") or []:
            if step.get("id") not in ("migrate_repos", "convert_pipelines", "validate"):
                continue
            result = step.get("result") or {}
            for detail in result.get("repo_details") or []:
                outcomes.append({
                    "run_id": run_dict.get("id"),
                    "run_name": run_dict.get("name"),
                    "run_status": run_dict.get("status"),
                    "dry_run": run_dict.get("dry_run", True),
                    "phase": run_dict.get("phase"),
                    "step": step.get("id"),
                    "repo": detail.get("repo"),
                    "status": detail.get("status"),
                    "summary": detail.get("summary"),
                    "errors": detail.get("errors") or [],
                })
            for item in result.get("work_items") or []:
                repo = item.get("ado_repo") or item.get("repo")
                if not repo:
                    continue
                outcomes.append({
                    "run_id": run_dict.get("id"),
                    "run_name": run_dict.get("name"),
                    "run_status": run_dict.get("status"),
                    "dry_run": run_dict.get("dry_run", True),
                    "phase": run_dict.get("phase"),
                    "step": step.get("id"),
                    "repo": repo,
                    "status": item.get("status"),
                    "summary": item.get("blocker") or item.get("description"),
                    "errors": [item.get("blocker")] if item.get("blocker") else [],
                })
    return outcomes


def build_migration_status_report(
    db: StateStore,
    *,
    pipeline_runs: list[Any] | None = None,
) -> dict[str, Any]:
    """Build the migration progress report shown on the status dashboard.

    Args:
        db: State store holding the migration rows and repo counts.
        pipeline_runs: Recent pipeline runs to derive per-repo run outcomes
            from. Omitted or empty means no run outcomes are reported.

    Returns:
        A report with a ``summary`` of tracked, git-migrated, failed and
        partial repo counts alongside the store's own completed and failed
        totals, the ``migrated_repos``, ``failed_repos``, ``partial_repos`` and
        ``all_repos`` lists (each repo carrying its per-scope statuses, roll-up
        status and git completion time), and the 30 most recent
        ``recent_run_outcomes``.
    """
    repos_map = _collect_repo_scope_rows(db)
    counts = db.get_migration_repo_counts()
    repos = []
    for key, entry in sorted(repos_map.items()):
        rollup = _rollup_repo_status(entry["scopes"])
        git_scope = entry["scopes"].get("repo") or entry["scopes"].get("git") or {}
        repos.append({
            **entry,
            "rollup_status": rollup,
            "git_migrated": git_scope.get("status") == "completed",
            "git_completed_at": git_scope.get("completed_at"),
        })

    migrated = [r for r in repos if r["git_migrated"]]
    failed = [r for r in repos if r["rollup_status"] == "failed"]
    partial = [r for r in repos if r["rollup_status"] == "partial"]
    run_outcomes = extract_outcomes_from_pipeline_runs(pipeline_runs or [])

    return {
        "summary": {
            "total_tracked_repos": len(repos),
            "git_migrated_count": len(migrated),
            "failed_count": len(failed),
            "partial_count": len(partial),
            "completed_repos": counts.get("completed_repos", 0),
            "failed_repos": counts.get("failed_repos", 0),
        },
        "migrated_repos": migrated,
        "failed_repos": failed,
        "partial_repos": partial,
        "all_repos": repos,
        "recent_run_outcomes": run_outcomes[:30],
    }


