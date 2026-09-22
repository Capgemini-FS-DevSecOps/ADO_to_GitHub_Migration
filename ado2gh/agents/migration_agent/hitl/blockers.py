"""Blocked work-item tracking and plan-revision identity for operator-input resolution."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ado2gh.agents.migration_agent.utils import coerce_dry_run
from ado2gh.api.migration_work_plan import sync_work_item_wire_keys


def _blocker_text(work_item: dict[str, Any]) -> str:
    return str(work_item.get("blocker") or work_item.get("block_reason") or "").strip()


_FIELD_SEPARATOR = "\x1f"

#: Length, in hex characters, of the configuration-file content fingerprint folded
#: into the plan revision key. Short enough to stay cheap to carry around, long
#: enough that two different configuration files colliding is not a practical
#: concern.
_CONFIG_FINGERPRINT_LENGTH = 16


def _config_content_fingerprint(config_path: str) -> str:
    """Short hash of the configuration file's contents, not just its name.

    Execution reads organisation defaults and destinations from this file's
    contents (GAP-130), so an edit to the file between approval and execution
    must revoke the approval even though the file NAME did not change. Returns
    the empty string when the path is blank, the file is missing, or it cannot
    be read — this is an identity input, never something that raises.

    Args:
        config_path: Path to the migration configuration file.

    Returns:
        A short hex digest of the file's bytes, or ``""``.
    """
    if not config_path:
        return ""
    from pathlib import Path

    try:
        digest = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    except OSError:
        return ""
    return digest[:_CONFIG_FINGERPRINT_LENGTH]


def plan_revision_key(plan: dict[str, Any] | None) -> str:
    """Identify one revision of a migration plan by the decisions it asks the operator to make.

    The digest covers every plan property a live write is authorised against —
    the plan id and revision counter, the execution mode, the config path the
    executor resolves organisation defaults from plus a content fingerprint of
    that file (GAP-130: an edit to the file between approval and execution
    must revoke the approval even when its name did not change), the enabled
    scope set, and for every repository and work item every coordinate
    :func:`resolve_repo_context <ado2gh.agents.migration_agent.nodes.executor.scope.resolve_repo_context>`
    reads to pick the source and destination of a live write — identifier,
    ADO project and repository name, target organisation, target repository,
    target reference (``gh_target``) and (for work items) migrated scope —
    and deliberately excludes mutable execution state such as work item
    ``status``, so running the plan never invalidates the approval that
    authorised the run (THR-09-002, THR-09-005). Repository and work-item
    entries are sorted before hashing so the digest is stable regardless of
    the order the plan lists them in.

    Args:
        plan: The migration plan, or None when the session has none.

    Returns:
        A short stable digest, or ``""`` when there is no plan to identify.
    """
    if not isinstance(plan, dict) or not plan:
        return ""

    try:
        from ado2gh.api.settings_store import SettingsStore

        config_path = SettingsStore().load().advanced.config_path or "migration.yaml"
    except Exception:
        config_path = ""

    plan_scopes = sorted(str(s) for s in (plan.get("enabled_scopes") or []))
    parts = [
        str(plan.get("plan_id") or ""),
        str(plan.get("revision", 0)),
        str(coerce_dry_run(plan.get("dry_run"), default=True)),
        config_path,
        _config_content_fingerprint(config_path),
        _FIELD_SEPARATOR.join(plan_scopes),
    ]

    repo_entries: list[str] = []
    for repo in plan.get("repos") or []:
        if isinstance(repo, dict):
            repo_scopes = sorted(
                str(s) for s in (repo.get("enabled_scopes") or repo.get("scopes") or [])
            )
            repo_entries.append(
                _FIELD_SEPARATOR.join([
                    str(repo.get("id") or repo.get("repository_id") or repo.get("name") or ""),
                    # Source coordinates `resolve_repo_context` (nodes/executor/scope.py)
                    # reads independently of `id` when it resolves which ADO repository a
                    # live write reads from.
                    str(repo.get("project") or ""),
                    str(repo.get("repo_name") or repo.get("name") or ""),
                    str(repo.get("gh_org") or repo.get("github_org") or ""),
                    str(repo.get("gh_repo") or repo.get("github_repo") or ""),
                    *repo_scopes,
                ])
            )
        else:
            repo_entries.append(str(repo))
    parts.extend(sorted(repo_entries))

    work_item_entries: list[str] = []
    for work_item in plan.get("work_items") or []:
        if isinstance(work_item, dict):
            work_item_entries.append(
                _FIELD_SEPARATOR.join([
                    str(work_item.get("repo") or ""),
                    str(work_item.get("scope") or ""),
                    # Source coordinates `resolve_repo_context` reads independently of
                    # `repo` when it resolves which ADO repository a live write reads from.
                    str(work_item.get("project") or ""),
                    str(work_item.get("repo_name") or ""),
                    str(work_item.get("github_org") or work_item.get("gh_org") or ""),
                    str(work_item.get("github_repo") or work_item.get("gh_repo") or ""),
                    # `gh_target` can supply the GitHub destination on its own when
                    # `github_org`/`github_repo` are unset (`resolve_repo_context`).
                    str(work_item.get("gh_target") or ""),
                ])
            )
    parts.extend(sorted(work_item_entries))

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
