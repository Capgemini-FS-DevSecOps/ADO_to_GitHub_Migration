"""Agent migration plans aligned with the Migrate tab pipeline (spec 009).

The agent planner and executor follow the same five-step flow as manual migrations
in the UI: connect → analyze_deps → migrate_repos → convert_pipelines → validate.
"""
from __future__ import annotations

from typing import Any

from ado2gh.api.pipeline_models import AGENT_MIGRATION_PIPELINE_STEPS, resolve_pipeline_step_defs
from ado2gh.api.migration_work_plan import plan_narrative_from_work_items
from ado2gh.models import MigrationScope, RepoConfig

AGENT_PIPELINE_STEP_IDS: list[str] = [s["id"] for s in AGENT_MIGRATION_PIPELINE_STEPS]
SCOPE_TO_PIPELINE_STEP: dict[str, str] = {
    MigrationScope.SECRETS.value: "analyze_deps",
    MigrationScope.BRANCH_POLICIES.value: "convert_metadata",
    MigrationScope.WIKI.value: "convert_metadata",
    MigrationScope.WORK_ITEMS.value: "convert_metadata",
    MigrationScope.REPO.value: "migrate_repos",
    MigrationScope.PIPELINES.value: "convert_pipelines",
    # Legacy agent scope alias
    "git": "migrate_repos",
}


def resolve_migration_phase(
    session: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> str | None:
    """Return migration phase only when the operator or discovery assigned one."""
    for source in ((plan or {}).get("phase"), session.get("plan_phase")):
        if source and str(source).strip():
            return str(source).strip()
    return None


def agent_pipeline_step_defs() -> list[dict[str, str]]:
    """Return migrate-tab pipeline step metadata for agent plans."""
    return resolve_pipeline_step_defs(AGENT_PIPELINE_STEP_IDS)


def resolve_github_org(
    *,
    repo: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    repo_key: str | None = None,
) -> str:
    """Resolve target GitHub org from repo, session, plan, active profile, or migration.yaml."""
    candidates: list[Any] = []
    if repo:
        candidates.extend([repo.get("gh_org"), repo.get("github_org")])
    if session:
        candidates.extend([session.get("gh_org"), session.get("github_org")])
    if plan and repo_key:
        from ado2gh.agents.migration_agent.utils import normalize_repo_key

        normalized_key = normalize_repo_key(repo_key)
        for entry in plan.get("repos") or []:
            if not isinstance(entry, dict):
                continue
            rid = normalize_repo_key(
                str(entry.get("id") or entry.get("repository_id") or "")
            )
            if rid and rid != normalized_key:
                continue
            candidates.extend([entry.get("gh_org"), entry.get("github_org")])
            break
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    try:
        from ado2gh.api.settings_store import SettingsStore
        from ado2gh.core.config_loader import ConfigLoader

        store = SettingsStore()
        active = store.get_active_profile()
        if active and str(active.gh_org or "").strip():
            return str(active.gh_org).strip()
        config_path = store.load().advanced.config_path or "migration.yaml"
        global_cfg, _ = ConfigLoader.load(config_path)
        return str(global_cfg.get("gh_org") or "").strip()
    except Exception:
        return ""


def repo_config_from_discovery(repo: dict[str, Any], session: dict[str, Any]) -> RepoConfig:
    """Build a RepoConfig from a discovery repo dict."""
    project = str(repo.get("project", "") or "").strip()
    repo_name = str(repo.get("repo_name") or repo.get("name") or "").strip()
    repo_key = f"{project}/{repo_name}" if project and repo_name else ""
    gh_org = resolve_github_org(repo=repo, session=session, repo_key=repo_key or None)
    gh_repo = str(repo.get("gh_repo") or repo.get("github_repo") or repo_name).strip()
    phase = str(
        repo.get("assigned_phase")
        or repo.get("phase")
        or session.get("plan_phase")
        or ""
    ).strip()
    return RepoConfig(
        ado_project=project,
        ado_repo=repo_name,
        gh_org=gh_org,
        gh_repo=gh_repo,
        phase=phase,
    )


def pipeline_counts_from_discovery(discovery: dict[str, Any] | None) -> dict[str, int]:
    """Map project/repo keys to pipeline_count from a discovery snapshot."""
    counts: dict[str, int] = {}
    if not isinstance(discovery, dict):
        return counts
    for repo in discovery.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        project = str(repo.get("project", "") or "").strip()
        name = str(repo.get("repo_name") or repo.get("name") or "").strip()
        repo_key = f"{project}/{name}" if project and name else str(repo.get("id") or "")
        if not repo_key:
            continue
        counts[repo_key] = int(repo.get("pipeline_count", 0) or 0)
    return counts


def pipeline_narrative_section() -> str:
    """Markdown bullet list of the five agent pipeline steps."""
    lines = ["**Pipeline steps** (same order as the Migrate tab):"]
    for idx, step in enumerate(AGENT_MIGRATION_PIPELINE_STEPS, start=1):
        lines.append(f"{idx}. **{step['label']}** — {step['description']}")
    return "\n".join(lines)


_MAX_CONFIRMATION_REPO_NAMES = 3


def _repo_display_ids(plan: dict[str, Any], session: dict[str, Any]) -> list[str]:
    repos = plan.get("repos") or []
    ids: list[str] = []
    for repo in repos:
        if isinstance(repo, dict):
            rid = repo.get("id") or repo.get("repository_id")
            if not rid:
                project = str(repo.get("project", "") or "").strip()
                name = str(repo.get("repo_name") or repo.get("name") or "").strip()
                if project and name:
                    rid = f"{project}/{name}"
            if rid:
                ids.append(str(rid))
        elif repo:
            ids.append(str(repo))
    if not ids and session.get("plan_repository_id"):
        ids.append(str(session["plan_repository_id"]))
    if not ids and session.get("plan_repository_ids"):
        ids.extend(str(r) for r in session["plan_repository_ids"])
    return ids


def plan_confirmation_summary(plan: dict[str, Any], session: dict[str, Any]) -> str:
    """Short operator-facing plan summary for confirmation (not full work-item lists)."""
    dry_run = bool(plan.get("dry_run", session.get("dry_run", True)))
    mode = "dry-run" if dry_run else "live"
    phase = resolve_migration_phase(session, plan)
    repo_ids = _repo_display_ids(plan, session)
    repo_count = int(plan.get("repo_count", len(repo_ids) or len(plan.get("repos") or [])))
    if repo_count < 1 and repo_ids:
        repo_count = len(repo_ids)

    primary = (
        session.get("plan_repository_id")
        or plan.get("repository_id")
        or (repo_ids[0] if len(repo_ids) == 1 else None)
    )

    lines: list[str] = []
    if repo_count <= 1 and primary:
        lines.append(f"### Migration plan — `{primary}` ({mode})")
    else:
        lines.append(f"### Migration plan — {repo_count} repositories ({mode})")
        if phase:
            lines.append("")
            lines.append(f"- **Phase:** {phase}")

    lines.append(f"- **Repositories:** {repo_count}")

    if repo_count == 1 and primary:
        repos = plan.get("repos") or []
        if repos and isinstance(repos[0], dict):
            gh_org = str(repos[0].get("gh_org") or repos[0].get("github_org") or "").strip()
            gh_repo = str(repos[0].get("gh_repo") or repos[0].get("github_repo") or "").strip()
            if gh_org and gh_repo:
                lines.append(f"- **GitHub target:** `{gh_org}/{gh_repo}`")
    elif repo_count > 1 and repo_ids:
        shown = ", ".join(f"`{rid}`" for rid in repo_ids[:_MAX_CONFIRMATION_REPO_NAMES])
        if repo_count > _MAX_CONFIRMATION_REPO_NAMES:
            shown += f", and {repo_count - _MAX_CONFIRMATION_REPO_NAMES} more"
        lines.append(f"- **Includes:** {shown}")

    lines.append("")
    lines.append(
        "Dependencies (service connections, wiki, branch policies) are included in the plan "
        "only when discovery or analyze_deps detects them for this repository."
    )
    from ado2gh.agents.migration_agent.message_format import strip_emojis

    return strip_emojis("\n".join(lines))


def finalize_agent_migration_plan(
    plan: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """Attach migrate-tab pipeline metadata and operator-facing narrative to a plan."""
    import re

    phase = resolve_migration_phase(session, plan)
    dry_run = bool(plan.get("dry_run", session.get("dry_run", True)))
    repository_id = (
        session.get("plan_repository_id")
        or plan.get("repository_id")
        or (plan.get("repos") or [{}])[0].get("id")
        if plan.get("repos")
        else None
    )

    plan["pipeline_steps"] = agent_pipeline_step_defs()
    plan["pipeline_step_ids"] = AGENT_PIPELINE_STEP_IDS
    if phase:
        plan["phase"] = phase
    else:
        plan.pop("phase", None)

    if repository_id:
        plan["repository_id"] = repository_id

    assumptions: list[str] = []
    for item in plan.get("assumptions") or []:
        text = str(item).strip()
        if not text:
            continue
        if not phase and re.search(r"\bphase\b", text, re.IGNORECASE):
            continue
        assumptions.append(text)
    plan["assumptions"] = assumptions

    work_items = plan.get("work_items") or []
    narrative_phase = phase or ""
    if work_items and all(isinstance(wi, dict) and wi.get("label") for wi in work_items):
        plan["narrative"] = plan_narrative_from_work_items(
            narrative_phase,
            work_items,
            dry_run=dry_run,
            repository_id=repository_id,
            include_blocked=False,
        )
    else:
        mode = "dry-run" if dry_run else "live"
        if repository_id:
            target = f"`{repository_id}`"
        elif phase:
            target = f"phase **{phase}**"
        else:
            target = "selected repositories"
        plan["narrative"] = (
            f"### Migration plan — {target} ({mode})\n\n"
            f"- **Repositories:** {plan.get('repo_count', len(plan.get('repos', [])))}"
        )

    plan["confirmation_summary"] = plan_confirmation_summary(plan, session)
    plan["repo_count"] = plan.get("repo_count", len(plan.get("repos", [])))
    return plan


def work_item_scopes(work_item: dict[str, Any]) -> list[str]:
    """Return scope names for a work item (supports legacy and per-scope shapes)."""
    if work_item.get("scope"):
        return [str(work_item["scope"])]
    return list(work_item.get("scopes") or [MigrationScope.REPO.value])
