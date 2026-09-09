"""Format migration/validation results for pipeline logs and UI."""
from __future__ import annotations

from typing import Any

from ado2gh.api.migration_work_plan import SCOPE_META


def migrate_repo_detail(key: str, res: dict[str, Any]) -> dict[str, Any]:
    """Normalize per-repo migration result for API/UI."""
    scopes = res.get("scopes") or {}
    scope_rows = []
    for scope, detail in scopes.items():
        meta = SCOPE_META.get(scope, {})
        scope_rows.append({
            "scope": scope,
            "label": meta.get("label", scope),
            "category": meta.get("category", "convert_metadata"),
            "status": detail.get("status", "?"),
            "error": detail.get("error"),
            "detail": build_scope_detail_message(scope, detail),
        })
    errors = list(res.get("errors") or [])
    for row in scope_rows:
        if row.get("status") != "completed":
            msg = row.get("error") or row.get("detail") or f"{row['scope']} failed"
            if msg not in errors:
                errors.append(str(msg))
        elif row.get("error") and row["error"] not in errors:
            errors.append(row["error"])
    return {
        "repo": key,
        "status": res.get("status", "unknown"),
        "errors": errors,
        "scopes": scope_rows,
        "summary": _migrate_summary(key, res, scope_rows, errors),
    }


def _migrate_summary(
    key: str,
    res: dict[str, Any],
    scope_rows: list[dict[str, Any]],
    errors: list[str],
) -> str:
    """Compose the one-line summary of a repo's migration result.

    Args:
        key: ``"<project>/<repo>"`` identifier for the repo.
        res: Raw migration result for that repo.
        scope_rows: Normalised per-scope rows built by
            :func:`migrate_repo_detail`.
        errors: Errors already collected for the repo.

    Returns:
        ``"<repo>: completed"`` on a clean run, otherwise the repo, its status
        and a semicolon-joined list of the failing scopes with their reasons,
        falling back to the plain error list when no scope rows exist.
    """
    status = res.get("status", "?")
    if status == "completed" and not errors:
        return f"{key}: completed"
    if scope_rows:
        failed = [s for s in scope_rows if s.get("status") != "completed"]
        if failed:
            parts = []
            for s in failed:
                label = s.get("label") or s["scope"]
                parts.append(f"{label}={s.get('error') or s.get('detail') or 'failed'}")
            return f"{key}: {status} — " + "; ".join(parts)
    if errors:
        return f"{key}: {status} — " + "; ".join(errors)
    return f"{key}: {status}"


def validation_repo_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise one repo's validation result for the API and the UI.

    Args:
        row: Raw validation row for a single repo, holding its ADO and GitHub
            names, its overall verdict and its per-check results.

    Returns:
        A row with the ADO project and repo, the GitHub target, the overall
        verdict, the flattened ``checks`` list, and a human reason repeated as
        ``primary_reason``, ``message`` and ``detail`` — the recorded error if
        there is one, else the first failure, else the first warning, else a
        digest of the passing checks.
    """
    checks = row.get("checks") or {}
    check_rows = []
    for name, check in checks.items():
        check_rows.append({
            "check": name,
            "verdict": check.get("verdict", "?"),
            "detail": check.get("detail", ""),
        })
    failures = [c for c in check_rows if c["verdict"] == "FAIL"]
    warns = [c for c in check_rows if c["verdict"] == "WARN"]
    primary = row.get("error")
    if not primary and failures:
        primary = failures[0]["detail"]
    elif not primary and warns:
        primary = warns[0]["detail"]
    elif not primary and check_rows:
        passed_details = [
            c["detail"] for c in check_rows
            if c.get("verdict") == "PASS" and c.get("detail")
        ]
        primary = "; ".join(passed_details[:4]) if passed_details else "All checks passed"
    else:
        primary = primary or "OK"
    return {
        "project": row.get("ado_project", ""),
        "repo": row.get("ado_repo", ""),
        "gh_target": row.get("gh_target", ""),
        "overall": row.get("overall", "?"),
        "primary_reason": primary,
        "message": primary,
        "detail": primary,
        "checks": check_rows,
    }


def build_validation_message(results: list[dict[str, Any]]) -> str:
    """Summarise a validation run in one line for logs and the run timeline.

    Args:
        results: Validation rows, each carrying an ``overall`` verdict of
            ``PASS``, ``FAIL`` or ``WARN``.

    Returns:
        A line of the form ``"Validated: <n>/<total> passed"``, extended with
        the count and names of up to five failing repos (and a ``+N more``
        suffix beyond that) and with the number of warnings.
    """
    passed = sum(1 for r in results if r.get("overall") == "PASS")
    failed = [r for r in results if r.get("overall") == "FAIL"]
    warned = [r for r in results if r.get("overall") == "WARN"]
    parts = [f"{passed}/{len(results)} passed"]
    if failed:
        names = [f"{r.get('ado_project')}/{r.get('ado_repo')}" for r in failed[:5]]
        suffix = f" (+{len(failed) - 5} more)" if len(failed) > 5 else ""
        parts.append(f"{len(failed)} failed: {', '.join(names)}{suffix}")
    if warned:
        parts.append(f"{len(warned)} warnings")
    return "Validated: " + "; ".join(parts)


def build_scope_detail_message(scope: str, detail: dict[str, Any]) -> str:
    """Pick the most useful human detail line for one migrated scope.

    Args:
        scope: Migration scope the detail belongs to.
        detail: Raw per-scope result, whose own ``detail`` may be a message
            dict, a stats dict or a plain value.

    Returns:
        The explicit message when the scope reported one, else the workflow
        branch and pull request URL (or the push error) for the pipelines
        scope, else the recorded error, else a short rendering of whatever the
        scope reported.
    """
    stats = detail.get("detail")
    if isinstance(stats, dict):
        if stats.get("message"):
            return str(stats["message"])
        if scope == "pipelines" and stats.get("pr_url"):
            branch = stats.get("workflow_branch", "ado2gh/migrated-workflows")
            return f"Pushed to {branch}: {stats['pr_url']}"
        if scope == "pipelines" and stats.get("push_error"):
            return str(stats["push_error"])
    if detail.get("error"):
        return str(detail["error"])
    return _stringify(detail.get("detail"))


def _stringify(val: object) -> str:
    """Render an arbitrary scope detail value as a short display string.

    Args:
        val: The value to render; commonly ``None``, a stats dict or a string.

    Returns:
        An empty string for ``None``, a ``"failed=<n> total=<n>"`` line for a
        dict reporting failures, otherwise the value's string form truncated to
        200 characters.
    """
    if val is None:
        return ""
    if isinstance(val, dict):
        if val.get("failed"):
            return f"failed={val.get('failed')} total={val.get('total', '?')}"
        return str(val)[:200]
    return str(val)[:200]
