"""Migration progress report from StateDB and recent pipeline runs."""
from __future__ import annotations

from typing import Any

from ado2gh.api.migration_work_plan import SCOPE_META


def _collect_repo_scope_rows(db) -> dict[str, dict[str, Any]]:
    """Latest migration row per ADO repo + scope."""
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


def _pipeline_run_repo_outcomes(runs: list[Any]) -> list[dict[str, Any]]:
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
    db,
    *,
    pipeline_runs: list[Any] | None = None,
) -> dict[str, Any]:
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
    run_outcomes = _pipeline_run_repo_outcomes(pipeline_runs or [])

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


def format_migration_status_narrative(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    migrated = report.get("migrated_repos") or []
    failed = report.get("failed_repos") or []
    partial = report.get("partial_repos") or []
    run_outcomes = report.get("recent_run_outcomes") or []

    lines = ["### Migration status (from platform state)", ""]

    git_count = summary.get("git_migrated_count", 0)
    if git_count == 0 and not failed and not partial and not run_outcomes:
        lines.extend([
            "**No repositories have been successfully migrated to GitHub yet** "
            "(no completed git/repo scope in the state database).",
            "",
            "A migration *plan* being ready does not mean code has been pushed — "
            "run the migration pipeline (or check **Migration Monitor** for run results).",
        ])
        return "\n".join(lines)

    lines.append(
        f"- **Git migrated:** {git_count} repo(s)"
        + (f" (aggregate count: {summary.get('completed_repos', 0)})" if summary.get("completed_repos") else "")
    )
    if failed:
        lines.append(f"- **Failed:** {len(failed)} repo(s)")
    if partial:
        lines.append(f"- **Partial:** {len(partial)} repo(s)")

    if migrated:
        lines.extend(["", "#### Successfully migrated (git)"])
        for repo in migrated[:20]:
            gh = repo.get("gh_repo") or "—"
            when = repo.get("git_completed_at") or ""
            suffix = f" · {when}" if when else ""
            lines.append(f"- `{repo['ado_repo']}` → `{gh}`{suffix}")
        if len(migrated) > 20:
            lines.append(f"- … and {len(migrated) - 20} more")

    if failed:
        lines.extend(["", "#### Failed"])
        for repo in failed[:15]:
            err = (repo.get("errors") or ["see run logs"])[0]
            lines.append(f"- `{repo['ado_repo']}` — {err[:200]}")

    if partial:
        lines.extend(["", "#### Partially migrated"])
        for repo in partial[:15]:
            scope_bits = []
            for scope, detail in sorted((repo.get("scopes") or {}).items()):
                label = SCOPE_META.get(scope, {}).get("label", scope)
                scope_bits.append(f"{label}={detail.get('status', '?')}")
            lines.append(f"- `{repo['ado_repo']}` — {', '.join(scope_bits)}")

    recent_failures = [
        o for o in run_outcomes
        if o.get("status") not in ("completed", None) and not o.get("dry_run")
    ]
    if recent_failures:
        lines.extend(["", "#### Recent live run issues"])
        seen: set[str] = set()
        for outcome in recent_failures:
            repo = outcome.get("repo") or "?"
            key = f"{repo}:{outcome.get('summary', '')}"
            if key in seen:
                continue
            seen.add(key)
            summary_text = outcome.get("summary") or "; ".join(outcome.get("errors") or [])
            lines.append(f"- `{repo}` — {summary_text[:240]}")
            if len(seen) >= 8:
                break

    lines.extend([
        "",
        "_Status reflects StateDB migration records and recent pipeline runs — "
        "not the in-chat migration plan._",
    ])
    return "\n".join(lines)
