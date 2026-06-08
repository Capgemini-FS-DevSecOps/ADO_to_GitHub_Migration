"""Format migration/validation results for pipeline logs and UI."""
from __future__ import annotations

from typing import Any


def migrate_repo_detail(key: str, res: dict[str, Any]) -> dict[str, Any]:
    """Normalize per-repo migration result for API/UI."""
    scopes = res.get("scopes") or {}
    scope_rows = []
    for scope, detail in scopes.items():
        scope_rows.append({
            "scope": scope,
            "status": detail.get("status", "?"),
            "error": detail.get("error"),
            "detail": _stringify(detail.get("detail")),
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


def _migrate_summary(key: str, res: dict, scope_rows: list, errors: list) -> str:
    status = res.get("status", "?")
    if status == "completed" and not errors:
        return f"{key}: completed"
    if scope_rows:
        failed = [s for s in scope_rows if s.get("status") != "completed"]
        if failed:
            parts = [f"{s['scope']}={s.get('error') or s.get('detail') or 'failed'}" for s in failed]
            return f"{key}: {status} — " + "; ".join(parts)
    if errors:
        return f"{key}: {status} — " + "; ".join(errors)
    return f"{key}: {status}"


def validation_repo_detail(row: dict[str, Any]) -> dict[str, Any]:
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
    primary = (
        row.get("error")
        or (failures[0]["detail"] if failures else None)
        or (warns[0]["detail"] if warns else None)
        or "OK"
    )
    return {
        "project": row.get("ado_project", ""),
        "repo": row.get("ado_repo", ""),
        "gh_target": row.get("gh_target", ""),
        "overall": row.get("overall", "?"),
        "primary_reason": primary,
        "checks": check_rows,
    }


def validation_message(results: list[dict[str, Any]]) -> str:
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


def _stringify(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, dict):
        if val.get("failed"):
            return f"failed={val.get('failed')} total={val.get('total', '?')}"
        return str(val)[:200]
    return str(val)[:200]
