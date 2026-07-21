"""Blocked work-item tracking for operator-input resolution."""
from __future__ import annotations

import re
from typing import Any

from ado2gh.models import MigrationScope


def _blocker_text(work_item: dict[str, Any]) -> str:
    return str(work_item.get("blocker") or work_item.get("block_reason") or "").strip()


def blocker_key(work_item: dict[str, Any]) -> str | None:
    """Stable key for a blocked work item (scope + repo + blocker slug)."""
    if work_item.get("status") != "blocked":
        return None
    text = _blocker_text(work_item)
    if not text:
        return None
    scope = str(work_item.get("scope") or "unknown")
    repo = str(work_item.get("repo") or "")
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]
    if repo:
        return f"{scope}:{repo}:{slug}"
    return f"{scope}:{slug}"


def is_resolvable_blocker(work_item: dict[str, Any]) -> bool:
    return blocker_key(work_item) is not None


def outstanding_blockers(plan: dict[str, Any], session: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocked work items that still need operator resolution."""
    declined = set(session.get("declined_blocker_resolutions") or [])
    outstanding: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for wi in plan.get("work_items") or []:
        if not isinstance(wi, dict) or wi.get("status") != "blocked":
            continue
        key = blocker_key(wi)
        if not key or key in declined or key in seen_keys:
            continue
        seen_keys.add(key)
        outstanding.append({
            "key": key,
            "scope": wi.get("scope"),
            "label": wi.get("label"),
            "blocker": _blocker_text(wi),
            "repo": wi.get("repo"),
        })
    return outstanding


def needs_blocker_resolution(plan: dict[str, Any], session: dict[str, Any]) -> bool:
    return bool(outstanding_blockers(plan, session))


def apply_skip_blocked_scopes(
    plan: dict[str, Any],
    *,
    blocker_keys: list[str],
) -> dict[str, Any]:
    """Mark blocked work items as skipped when the operator declines resolution."""
    keys = set(blocker_keys)
    work_items = plan.get("work_items") or []
    for wi in work_items:
        if not isinstance(wi, dict):
            continue
        key = blocker_key(wi)
        if key in keys and wi.get("status") == "blocked":
            wi["status"] = "skipped"
            wi["blocker"] = ""
            wi["detail"] = "Skipped by operator"
    plan["work_items"] = work_items
    plan["blocked"] = any(
        wi.get("status") == "blocked" for wi in work_items if isinstance(wi, dict)
    )
    return plan


def record_declined_blockers(session: dict[str, Any], blocker_keys: list[str]) -> None:
    declined = list(session.get("declined_blocker_resolutions") or [])
    for key in blocker_keys:
        if key not in declined:
            declined.append(key)
    session["declined_blocker_resolutions"] = declined


def operator_declined_blocker(session: dict[str, Any], blocker_key: str) -> bool:
    return blocker_key in (session.get("declined_blocker_resolutions") or [])


def sanitize_plan_for_operator_view(
    plan: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """Remove blocked work items from operator-facing context unless declined."""
    if not isinstance(plan, dict):
        return {}
    declined = set(session.get("declined_blocker_resolutions") or [])
    copy = dict(plan)
    work_items = []
    for wi in plan.get("work_items") or []:
        if not isinstance(wi, dict):
            continue
        if wi.get("status") == "blocked":
            key = blocker_key(wi)
            if key and key not in declined:
                continue
        work_items.append(dict(wi))
    copy["work_items"] = work_items
    copy.pop("blocked_items", None)
    return copy
