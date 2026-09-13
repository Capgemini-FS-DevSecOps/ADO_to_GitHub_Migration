"""Blocked work-item tracking and plan-revision identity for operator-input resolution."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ado2gh.agents.migration_agent.utils import coerce_dry_run
from ado2gh.api.migration_work_plan import sync_work_item_wire_keys


def _blocker_text(work_item: dict[str, Any]) -> str:
    return str(work_item.get("blocker") or work_item.get("block_reason") or "").strip()


def plan_revision_key(plan: dict[str, Any] | None) -> str:
    """Identify one revision of a migration plan by the decisions it asks the operator to make.

    The digest covers what an operator answers *about* — the plan id and revision
    counter, the execution mode, the target repositories and the scope/repo pairs of
    the work items — and deliberately excludes mutable execution state such as work
    item ``status``, so running the plan never invalidates the approval that
    authorised the run (THR-09-002, THR-09-005).

    Args:
        plan: The migration plan, or None when the session has none.

    Returns:
        A short stable digest, or ``""`` when there is no plan to identify.
    """
    if not isinstance(plan, dict) or not plan:
        return ""
    parts = [
        str(plan.get("plan_id") or ""),
        str(plan.get("revision", 0)),
        str(coerce_dry_run(plan.get("dry_run"), default=True)),
    ]
    for repo in plan.get("repos") or []:
        if isinstance(repo, dict):
            parts.append(str(repo.get("id") or repo.get("repository_id") or repo.get("name") or ""))
        else:
            parts.append(str(repo))
    for work_item in plan.get("work_items") or []:
        if isinstance(work_item, dict):
            parts.append(f"{work_item.get('scope')}@{work_item.get('repo')}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _declined_key(plan: dict[str, Any] | None, key: str) -> str:
    """Scope a blocker key to the plan revision that raised it.

    Returns:
        ``"<plan-revision>::<blocker-key>"``, so a replan re-raises a blocker the
        operator declined on an earlier revision instead of silently skipping it.
    """
    return f"{plan_revision_key(plan)}::{key}"


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




def outstanding_blockers(plan: dict[str, Any], session: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocked work items that still need operator resolution."""
    declined = set(session.get("declined_blocker_resolutions") or [])
    outstanding: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for wi in plan.get("work_items") or []:
        if not isinstance(wi, dict) or wi.get("status") != "blocked":
            continue
        key = blocker_key(wi)
        if not key or _declined_key(plan, key) in declined or key in seen_keys:
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
    """Report whether the plan still has blockers the operator has not answered.

    Returns:
        True when at least one blocked work item is neither declined nor
        already resolved for this session.
    """
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
            sync_work_item_wire_keys(wi)
    plan["work_items"] = work_items
    plan["blocked"] = any(
        wi.get("status") == "blocked" for wi in work_items if isinstance(wi, dict)
    )
    return plan


def record_declined_blockers(
    session: dict[str, Any],
    blocker_keys: list[str],
    *,
    plan: dict[str, Any] | None = None,
) -> None:
    """Remember blocker keys the operator declined, scoped to the plan revision that raised them.

    Args:
        session: Live agent session, updated in place.
        blocker_keys: Blocker keys the operator chose not to resolve.
        plan: The plan revision the operator was answering about. Defaults to the
            session's current plan.
    """
    plan = plan if plan is not None else session.get("migration_plan")
    declined = list(session.get("declined_blocker_resolutions") or [])
    for key in blocker_keys:
        scoped = _declined_key(plan, key)
        if scoped not in declined:
            declined.append(scoped)
    session["declined_blocker_resolutions"] = declined




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
            if key and _declined_key(plan, key) not in declined:
                continue
        work_items.append(dict(wi))
    copy["work_items"] = work_items
    copy.pop("blocked_items", None)
    return copy
