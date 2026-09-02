"""planner_plan_builders.py module — migration queue and plan construction helpers."""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.utils import canonical_repo_id
from ado2gh.models import MigrationScope


def _build_migration_queue_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Build a migration queue from a plan's repos and work items."""
    plan_repos = plan.get("repos", [])
    work_items = [wi for wi in plan.get("work_items", []) if isinstance(wi, dict)]

    by_repo: dict[str, list[dict[str, Any]]] = {}
    for wi in work_items:
        repo_id = str(wi.get("repo") or "").strip()
        if repo_id:
            by_repo.setdefault(repo_id, []).append(wi)

    plan_ids: list[str] = []
    for repo in plan_repos:
        repo_id = canonical_repo_id(repo) if isinstance(repo, dict) else str(repo).strip()
        if repo_id and repo_id not in plan_ids:
            plan_ids.append(repo_id)

    if by_repo:
        repo_order: list[str] = []
        for repo_id in plan_ids:
            if repo_id in by_repo and repo_id not in repo_order:
                repo_order.append(repo_id)
        for repo_id in by_repo:
            if repo_id not in repo_order:
                repo_order.append(repo_id)
    else:
        repo_order = plan_ids

    queue_items = [
        {
            "repo_id": repo_id,
            "work_items": by_repo.get(repo_id, []),
            "status": "pending",
        }
        for repo_id in repo_order
    ]
    return {
        "items": queue_items,
        "current_index": 0,
        "completed": [],
        "failed": [],
    }


def _advance_migration_queue(
    migration_queue: dict[str, Any],
    *,
    repo_id: str = "",
) -> bool:
    """Advance queue after one repo slot is consumed. Returns True if advanced."""
    queue_items = migration_queue.get("items", [])
    current_index = int(migration_queue.get("current_index", 0) or 0)
    if current_index >= len(queue_items):
        return False
    migration_queue["current_index"] = current_index + 1
    completed = list(migration_queue.get("completed", []))
    resolved_repo = repo_id or str(queue_items[current_index].get("repo_id") or "").strip()
    if resolved_repo and resolved_repo not in completed:
        completed.append(resolved_repo)
    migration_queue["completed"] = completed
    return True


def _build_heuristic_plan(
    repos: list[dict[str, Any]],
    session: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    """Build a migration plan using topological sort and migrate-tab pipeline steps."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import (
        finalize_agent_migration_plan,
        pipeline_counts_from_discovery,
        repo_config_from_discovery,
    )

    dry_run = session.get("dry_run", True)

    sorted_repos = []
    remaining = list(repos)
    processed = set()

    for _ in range(len(repos) + 1):
        if not remaining:
            break
        for repo in list(remaining):
            repo_id = canonical_repo_id(repo) if isinstance(repo, dict) else str(repo)
            deps = repo.get("dependencies", []) if isinstance(repo, dict) else []
            if all(d in processed for d in deps):
                sorted_repos.append(repo)
                processed.add(repo_id)
                remaining.remove(repo)

    sorted_repos.extend(remaining)

    repo_configs = [
        repo_config_from_discovery(r, session) for r in sorted_repos if isinstance(r, dict)
    ]
    pipeline_counts = pipeline_counts_from_discovery(session.get("discovery_snapshot"))
    from ado2gh.agents.migration_agent.nodes.executor.scope import build_agent_work_items_for_session

    work_items = build_agent_work_items_for_session(
        repo_configs,
        session,
        db=None,
        repo_pipeline_counts=pipeline_counts,
    ) if repo_configs else []

    plan_repos: list[dict[str, Any]] = []
    for repo_dict, cfg in zip(
        [r for r in sorted_repos if isinstance(r, dict)],
        repo_configs,
    ):
        entry: dict[str, Any] = {
            "id": canonical_repo_id(repo_dict),
            "name": repo_dict.get("name") or repo_dict.get("repo_name", ""),
            "gh_org": cfg.gh_org,
            "gh_repo": cfg.gh_repo,
            "github_org": cfg.gh_org,
            "github_repo": cfg.gh_repo,
        }
        plan_repos.append(entry)

    plan = {
        "repos": plan_repos,
        "work_items": work_items,
        "dry_run": dry_run,
        "assumptions": [
            "Plan follows the Migrate tab pipeline: discovery → dependencies → "
            "migrate repos → convert workflows → validate",
        ],
        "blocked_items": [r for r in repos if isinstance(r, dict) and r.get("blocked")],
        "revision": revision,
        "repo_count": len(sorted_repos),
    }
    return finalize_agent_migration_plan(plan, session)


def _build_migration_plan_from_llm(
    parsed: dict[str, Any],
    session: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    """Build a migration plan from LLM-parsed JSON output."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import (
        finalize_agent_migration_plan,
        pipeline_counts_from_discovery,
        repo_config_from_discovery,
    )

    raw_repos = parsed.get("repos", [])
    repos = [
        {
            "id": r.get("id", r.get("name", "")),
            "name": r.get("name", ""),
            "project": r.get("project", ""),
            "repo_name": r.get("repo_name", r.get("name", "")),
        }
        if isinstance(r, dict)
        else {"id": str(r), "name": str(r), "project": "", "repo_name": str(r)}
        for r in raw_repos
    ]
    raw_work_items = parsed.get("work_items", [])
    repo_configs = [repo_config_from_discovery(r, session) for r in repos]
    pipeline_counts = pipeline_counts_from_discovery(session.get("discovery_snapshot"))
    if repo_configs:
        from ado2gh.agents.migration_agent.nodes.executor.scope import build_agent_work_items_for_session

        work_items = build_agent_work_items_for_session(
            repo_configs,
            session,
            db=None,
            repo_pipeline_counts=pipeline_counts,
            plan=parsed,
        )
    elif raw_work_items and all(
        isinstance(wi, dict) and wi.get("label") for wi in raw_work_items
    ):
        work_items = [
            wi if isinstance(wi, dict)
            else {"repo": str(wi), "scope": MigrationScope.REPO.value, "status": "ready"}
            for wi in raw_work_items
        ]
    else:
        work_items = []
    plan = {
        "repos": repos,
        "work_items": work_items,
        "dry_run": parsed.get("dry_run", session.get("dry_run", True)),
        "assumptions": parsed.get("assumptions", []),
        "blocked_items": parsed.get("blocked_items", []),
        "revision": revision,
        "repo_count": len(repos),
        "pipeline_step_ids": parsed.get("pipeline_step_ids"),
    }
    return finalize_agent_migration_plan(plan, session)
