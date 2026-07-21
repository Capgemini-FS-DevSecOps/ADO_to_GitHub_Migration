"""Build descriptive per-repo migration work items (scopes, blockers, categories)."""
from __future__ import annotations

import re
from typing import Any

from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.reporting.pipeline_readiness import PipelineReadinessReport

SCOPE_META: dict[str, dict[str, str]] = {
    MigrationScope.REPO.value: {
        "label": "Migrate repository (git / GEI)",
        "category": "migrate_repo",
        "description": "Transfer branches, tags, and commit history to GitHub",
    },
    MigrationScope.PIPELINES.value: {
        "label": "Convert pipelines → GitHub Actions",
        "category": "convert_metadata",
        "description": (
            "Transform ADO YAML and push workflows to the "
            "`ado2gh/migrated-workflows` branch (opens a PR)"
        ),
    },
    MigrationScope.SECRETS.value: {
        "label": "Map secrets & service connections",
        "category": "manual_setup",
        "description": "Map ADO service connections to GitHub secrets (agent provisions via accelerator when mappings are supplied)",
    },
    MigrationScope.BRANCH_POLICIES.value: {
        "label": "Convert branch policies",
        "category": "convert_metadata",
        "description": "Map ADO branch policies to GitHub branch protection rules",
    },
    MigrationScope.WIKI.value: {
        "label": "Migrate wiki",
        "category": "convert_metadata",
        "description": "Copy ADO wiki content to GitHub wiki",
    },
    MigrationScope.WORK_ITEMS.value: {
        "label": "Migrate work items → GitHub issues",
        "category": "convert_metadata",
        "description": "Create GitHub issues from ADO work items",
    },
}

CATEGORY_LABELS = {
    "migrate_repo": "Repository migration",
    "convert_metadata": "Metadata conversion",
    "manual_setup": "Manual setup required",
    "validate": "Post-migration validation",
}


def apply_operator_secret_mappings(
    work_items: list[dict[str, Any]],
    mappings_or_profile: str | dict[str, str],
    settings_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Mark secrets work items ready when operator supplied GitHub secret names."""
    if isinstance(mappings_or_profile, dict):
        mappings = {
            str(k): str(v).strip()
            for k, v in mappings_or_profile.items()
            if str(v).strip()
        }
    else:
        mappings = {}
        if settings_store is not None:
            mappings = settings_store.get_operator_resolutions(mappings_or_profile) or {}
    if not mappings:
        return work_items
    for wi in work_items:
        if wi.get("scope") != MigrationScope.SECRETS.value or wi.get("status") != "blocked":
            continue
        blocker = wi.get("blocker") or ""
        if "inventory" in blocker.lower():
            continue
        unresolved = False
        for key, value in mappings.items():
            if not value.strip():
                unresolved = True
        if not unresolved and mappings:
            wi["status"] = "ready"
            wi["blocker"] = ""
            wi["detail"] = f"Operator mapped {len(mappings)} secret(s)"
    return work_items


def collect_secret_gap_fields(
    discovery: dict[str, Any] | None,
    work_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build form fields for service connections that block secrets scope."""
    gaps = (discovery or {}).get("inventory_gaps") or []
    blocked = [wi for wi in work_items if wi.get("scope") == MigrationScope.SECRETS.value and wi.get("status") == "blocked"]
    if not blocked:
        return []
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gap in gaps:
        if gap.get("type") != "service_connection":
            continue
        name = gap.get("name", "")
        field = gap.get("field") or f"secret_mapping__{gap.get('project', '')}__{name}"
        if not name or field in seen:
            continue
        seen.add(field)
        fields.append({
            "name": field,
            "label": f"{gap.get('project')}: {name} → GitHub secret",
            "type": "text",
            "required": True,
        })
        if len(fields) >= 12:
            break
    if not fields:
        for wi in blocked:
            blocker = wi.get("blocker") or ""
            if "Service connection '" in blocker:
                match = re.search(r"Service connection '([^']+)'", blocker)
                if match:
                    sc_name = match.group(1)
                    field = f"secret_mapping__{sc_name}"
                    if field not in seen:
                        seen.add(field)
                        fields.append({
                            "name": field,
                            "label": f"GitHub secret for ADO connection '{sc_name}'",
                            "type": "text",
                            "required": True,
                        })
    return fields


def _work_item_id(repo_key: str, scope: str) -> str:
    safe = repo_key.replace("/", "__")
    return f"{safe}:{scope}"


def _pipeline_blockers_for_repo(db, project: str, repo_name: str) -> list[str]:
    blockers: list[str] = []
    try:
        pipelines = db.get_pipelines_for_repo(project, repo_name)
    except Exception:
        return blockers
    if not pipelines:
        return blockers
    report = PipelineReadinessReport(db)
    for pipe in pipelines:
        assessment = report._assess_pipeline(pipe)
        for b in assessment.get("blockers", []):
            blockers.append(f"{pipe.pipeline_name}: {b}")
        for w in assessment.get("warnings", []):
            if "variable group" in w.lower():
                blockers.append(f"{pipe.pipeline_name}: {w}")
    return blockers[:8]


def _secrets_blockers(db, project: str, repo_name: str) -> list[str]:
    blockers: list[str] = []
    try:
        pipelines = db.get_pipelines_for_repo(project, repo_name)
    except Exception:
        return blockers
    sc_names: set[str] = set()
    vg_names: set[str] = set()
    for pipe in pipelines:
        for sc in pipe.service_connections or []:
            name = sc.get("name") if isinstance(sc, dict) else str(sc)
            if name:
                sc_names.add(name)
        for vg in pipe.variable_groups or []:
            name = vg.get("name") if isinstance(vg, dict) else str(vg)
            if name:
                vg_names.add(name)
    for name in sorted(sc_names)[:5]:
        blockers.append(
            f"Service connection '{name}' — create matching GitHub secret or OIDC login"
        )
    for name in sorted(vg_names)[:5]:
        blockers.append(
            f"Variable group '{name}' — run `gh secret set` on the target repo"
        )
    if not blockers and not pipelines:
        blockers.append(
            "Run pipeline inventory first to discover service connections"
        )
    return blockers


def _scope_status(
    scope: str,
    *,
    enabled: bool,
    pipeline_blockers: list[str],
    secrets_blockers: list[str],
    pipeline_count: int,
) -> tuple[str, str]:
    """Return (status, blocker_message). status: ready | blocked | skipped."""
    if not enabled:
        return "skipped", "Not included in migration scopes"
    if scope == MigrationScope.PIPELINES.value:
        if pipeline_count == 0:
            return "blocked", "No pipelines in inventory — run pipeline inventory first"
        if pipeline_blockers:
            return "blocked", "; ".join(pipeline_blockers[:3])
        return "ready", ""
    if scope == MigrationScope.SECRETS.value:
        if secrets_blockers:
            return "blocked", "; ".join(secrets_blockers[:3])
        return "ready", ""
    return "ready", ""


def build_work_items_for_repos(
    repos: list[RepoConfig],
    *,
    enabled_scopes: list[str] | None = None,
    enabled_scopes_per_repo: dict[str, list[str]] | None = None,
    db=None,
    repo_pipeline_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """One work item per repo × scope with human labels and blocker hints."""
    scopes_order = [s.value for s in MigrationScope]
    default_enabled = set(enabled_scopes or [MigrationScope.REPO.value])
    per_repo = enabled_scopes_per_repo or {}
    items: list[dict[str, Any]] = []

    for repo in repos:
        repo_key = f"{repo.ado_project}/{repo.ado_repo}"
        repo_enabled = set(per_repo.get(repo_key) or default_enabled)
        pipeline_blockers = _pipeline_blockers_for_repo(db, repo.ado_project, repo.ado_repo) if db else []
        secrets_blockers = _secrets_blockers(db, repo.ado_project, repo.ado_repo) if db else []
        pipeline_count = 0
        if db:
            try:
                pipeline_count = len(db.get_pipelines_for_repo(repo.ado_project, repo.ado_repo))
            except Exception:
                pipeline_count = 0
        elif repo_pipeline_counts is not None:
            pipeline_count = int(repo_pipeline_counts.get(repo_key, 0) or 0)

        for scope in scopes_order:
            meta = SCOPE_META.get(scope, {"label": scope, "category": "convert_metadata", "description": ""})
            in_scope = scope in repo_enabled
            status, blocker = _scope_status(
                scope,
                enabled=in_scope,
                pipeline_blockers=pipeline_blockers,
                secrets_blockers=secrets_blockers,
                pipeline_count=pipeline_count,
            )
            items.append({
                "id": _work_item_id(repo_key, scope),
                "repo": repo_key,
                "project": repo.ado_project,
                "repo_name": repo.ado_repo,
                "gh_target": (
                    f"{repo.gh_org}/{repo.gh_repo}"
                    if (repo.gh_org or "").strip()
                    else repo.gh_repo
                ),
                "github_org": (repo.gh_org or "").strip(),
                "github_repo": repo.gh_repo,
                "scope": scope,
                "category": meta["category"],
                "category_label": CATEGORY_LABELS.get(meta["category"], meta["category"]),
                "label": f"{repo_key} — {meta['label']}",
                "description": meta["description"],
                "status": status,
                "blocker": blocker,
                "pipeline_count": pipeline_count if scope == MigrationScope.PIPELINES.value else None,
            })
    return items


def work_items_summary(work_items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ready": 0, "blocked": 0, "skipped": 0, "completed": 0, "failed": 0}
    for wi in work_items:
        st = wi.get("status", "ready")
        counts[st] = counts.get(st, 0) + 1
    return counts


def aggregate_work_items_for_timeline(work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-repo work items into scope-level rows with repo counts."""
    scopes_order = [s.value for s in MigrationScope]
    scope_rank = {scope: idx for idx, scope in enumerate(scopes_order)}
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}

    for wi in work_items:
        if wi.get("status") == "skipped":
            continue
        status = wi.get("status", "pending")
        if status == "ready":
            status = "pending"
        scope = wi.get("scope", "")
        category = wi.get("category", "")
        key = (scope, category, status)
        meta = SCOPE_META.get(scope, {})
        base_label = meta.get("label")
        if not base_label and " — " in (wi.get("label") or ""):
            base_label = wi["label"].split(" — ", 1)[1]
        base_label = base_label or wi.get("label") or scope

        if key not in groups:
            groups[key] = {
                "scope": scope,
                "category": category,
                "status": status,
                "label": base_label,
                "description": meta.get("description") or wi.get("description", ""),
                "category_label": wi.get("category_label")
                or CATEGORY_LABELS.get(category, category),
                "count": 0,
                "blockers": [],
            }
        group = groups[key]
        group["count"] += 1
        blocker = (wi.get("blocker") or "").strip()
        if blocker and blocker not in group["blockers"]:
            group["blockers"].append(blocker)

    aggregated: list[dict[str, Any]] = []
    for key in sorted(groups.keys(), key=lambda k: (scope_rank.get(k[0], 99), k[1], k[2])):
        group = groups[key]
        count = group["count"]
        repo_phrase = "1 repo" if count == 1 else f"{count} repos"
        label = f"{group['label']} · {repo_phrase}"
        blocker = ""
        if group["blockers"]:
            blocker = group["blockers"][0]
            if len(group["blockers"]) > 1:
                blocker += f" (+{len(group['blockers']) - 1} more)"
        scope, category, status = key
        aggregated.append({
            "id": f"scope:{scope}:{category}:{status}",
            "scope": scope,
            "category": category,
            "category_label": group["category_label"],
            "label": label,
            "description": group["description"],
            "status": status,
            "blocker": blocker,
            "count": count,
        })
    return aggregated


def apply_scope_results_to_work_items(
    work_items: list[dict[str, Any]],
    repo_details: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Update work item statuses from migrate step repo_details."""
    by_key: dict[tuple[str, str], dict] = {}
    for wi in work_items:
        by_key[(wi["repo"], wi["scope"])] = wi

    for detail in repo_details:
        repo = detail.get("repo", "")
        for row in detail.get("scopes") or []:
            scope = row.get("scope", "")
            key = (repo, scope)
            wi = by_key.get(key)
            if not wi:
                continue
            st = row.get("status", "")
            if st == "completed":
                wi["status"] = "completed"
                wi["blocker"] = ""
                wi["detail"] = row.get("detail") or "Completed"
            elif st == "failed":
                wi["status"] = "failed"
                wi["detail"] = row.get("error") or row.get("detail") or "Failed"
            elif st == "skipped":
                wi["status"] = "skipped"
    return work_items


def filter_work_items_for_scopes(
    work_items: list[dict[str, Any]],
    scopes: list[str] | None,
) -> list[dict[str, Any]]:
    """Return only work items for scopes executed in a pipeline step."""
    if scopes is None:
        return work_items
    allowed = set(scopes)
    return [wi for wi in work_items if wi.get("scope") in allowed]


def executable_work_items(work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Work items the executor should run now (ready only)."""
    result: list[dict[str, Any]] = []
    for wi in work_items:
        if not isinstance(wi, dict):
            continue
        status = wi.get("status")
        if status is None:
            status = "ready"
        if status == "ready":
            result.append(wi)
    return result


def group_work_items_by_repo(
    work_items: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group work items by repo id, preserving plan order."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for wi in work_items:
        if not isinstance(wi, dict):
            continue
        repo_id = str(wi.get("repo") or "").strip()
        if not repo_id:
            continue
        groups.setdefault(repo_id, []).append(wi)
    return groups


def scope_row_enriched(scope: str, detail: dict) -> dict[str, Any]:
    meta = SCOPE_META.get(scope, {})
    return {
        "scope": scope,
        "label": meta.get("label", scope),
        "category": meta.get("category", "convert_metadata"),
        "status": detail.get("status", "?"),
        "error": detail.get("error"),
        "detail": detail.get("detail"),
    }


def plan_narrative_from_work_items(
    phase: str | None,
    work_items: list[dict[str, Any]],
    *,
    dry_run: bool,
    repository_id: str | None = None,
    include_blocked: bool = True,
) -> str:
    summary = work_items_summary(work_items)
    mode = "dry-run" if dry_run else "live"
    repos = sorted({wi["repo"] for wi in work_items if wi.get("status") != "skipped"})
    blocked = [wi for wi in work_items if wi.get("status") == "blocked"]
    if repository_id:
        header = f"### Migration plan — `{repository_id}` ({mode})"
    elif phase:
        header = f"### Migration plan — phase **{phase}** ({mode})"
    else:
        header = f"### Migration plan ({mode})"
    lines = [
        header,
        "",
        f"- **Repositories:** {len(repos)}",
        f"- **Work items:** {len(work_items)} "
        f"({summary.get('ready', 0)} ready, {summary.get('blocked', 0)} blocked, "
        f"{summary.get('skipped', 0)} out of scope)",
        "",
    ]
    if include_blocked and blocked:
        lines.append("**Blocked (needs secrets, inventory, or manual setup):**")
        for wi in blocked[:8]:
            lines.append(f"- {wi['label']}: {wi.get('blocker', 'blocked')}")
        if len(blocked) > 8:
            lines.append(f"- …and {len(blocked) - 8} more")
        lines.append("")
    by_cat: dict[str, list] = {}
    for wi in work_items:
        if wi.get("status") == "skipped":
            continue
        if not include_blocked and wi.get("status") == "blocked":
            continue
        by_cat.setdefault(wi.get("category_label", "Other"), []).append(wi)
    for cat, wis in by_cat.items():
        lines.append(f"**{cat}**")
        for wi in wis[:6]:
            status = wi.get("status", "ready")
            lines.append(f"- [{status}] {wi['label']}")
        lines.append("")
    return "\n".join(lines).strip()
