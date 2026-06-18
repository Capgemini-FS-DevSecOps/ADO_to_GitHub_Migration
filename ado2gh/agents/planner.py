"""Migration planner — topo order, layout policy, workflow branch strategy."""
from __future__ import annotations

from typing import Any, List

from ado2gh.pipelines.dependency_graph import RepoDependencyEdge, sort_repo_order


class AgentPlanner:
    """Builds execution plans scoped to assignment cohorts."""

    WHITELIST_TOOLS = (
        "ado2gh_discover",
        "ado2gh_readiness",
        "ado2gh_plan_phase",
    )

    def plan(
        self,
        profile_id: str,
        assignment_repos: List[str],
        dependency_edges: List[dict],
        workflow_layout: str = "modular",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        edges = [
            RepoDependencyEdge(e["from_repo"], e["to_repo"], e.get("edge_type", "pipeline_resource"))
            for e in dependency_edges
        ]
        ordered, cycles = sort_repo_order(assignment_repos, edges)
        steps = [
            {"tool": "ado2gh_discover", "dry_run": dry_run},
            {"tool": "ado2gh_readiness", "dry_run": dry_run},
            {"tool": "ado2gh_plan_phase", "dry_run": dry_run},
        ]
        if not dry_run:
            steps.append({"tool": "ado2gh_enqueue_job", "requires_approver": True})
        return {
            "profile_id": profile_id,
            "repo_order": ordered,
            "cycles": cycles or [],
            "workflow_layout": workflow_layout,
            "workflow_branch": "ado2gh/migrated-workflows",
            "steps": steps,
            "dry_run": dry_run,
        }
