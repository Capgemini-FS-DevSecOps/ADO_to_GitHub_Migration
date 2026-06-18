"""Build descriptive per-repo migration work items (scopes, blockers, categories)."""
from __future__ import annotations

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
        "description": "Transform ADO YAML and push workflows to ado2gh/migrated-workflows",
    },
    MigrationScope.SECRETS.value: {
        "label": "Map secrets & service connections",
        "category": "manual_setup",
        "description": "Generate secrets manifest — values must be created in GitHub manually",
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
            if "service connection" in w.lower() or "variable group" in w.lower():
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
    db=None,
) -> list[dict[str, Any]]:
    """One work item per repo × scope with human labels and blocker hints."""
    scopes_order = [s.value for s in MigrationScope]
    enabled = set(enabled_scopes or [MigrationScope.REPO.value])
    items: list[dict[str, Any]] = []

    for repo in repos:
        repo_key = f"{repo.ado_project}/{repo.ado_repo}"
        pipeline_blockers = _pipeline_blockers_for_repo(db, repo.ado_project, repo.ado_repo) if db else []
        secrets_blockers = _secrets_blockers(db, repo.ado_project, repo.ado_repo) if db else []
        pipeline_count = 0
        if db:
            try:
                pipeline_count = len(db.get_pipelines_for_repo(repo.ado_project, repo.ado_repo))
            except Exception:
                pipeline_count = 0

        for scope in scopes_order:
            meta = SCOPE_META.get(scope, {"label": scope, "category": "convert_metadata", "description": ""})
            in_scope = scope in enabled
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
                "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
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
    phase: str,
    work_items: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> str:
    summary = work_items_summary(work_items)
    mode = "dry-run" if dry_run else "live"
    repos = sorted({wi["repo"] for wi in work_items if wi.get("status") != "skipped"})
    blocked = [wi for wi in work_items if wi.get("status") == "blocked"]
    lines = [
        f"### Migration plan — phase **{phase}** ({mode})",
        "",
        f"- **Repositories:** {len(repos)}",
        f"- **Work items:** {len(work_items)} "
        f"({summary.get('ready', 0)} ready, {summary.get('blocked', 0)} blocked, "
        f"{summary.get('skipped', 0)} out of scope)",
        "",
    ]
    if blocked:
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
        by_cat.setdefault(wi.get("category_label", "Other"), []).append(wi)
    for cat, wis in by_cat.items():
        lines.append(f"**{cat}**")
        for wi in wis[:6]:
            icon = {"ready": "○", "blocked": "⊘", "completed": "✓", "failed": "✗"}.get(
                wi.get("status", "ready"), "·",
            )
            lines.append(f"- {icon} {wi['label']}")
        lines.append("")
    return "\n".join(lines).strip()
