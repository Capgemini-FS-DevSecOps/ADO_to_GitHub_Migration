"""Tests for descriptive migration work-item planning."""
from __future__ import annotations

from unittest.mock import MagicMock

from ado2gh.api.migration_work_plan import (
    aggregate_work_items_for_timeline,
    apply_scope_results_to_work_items,
    build_work_items_for_repos,
    filter_work_items_for_scopes,
    plan_narrative_from_work_items,
    work_items_summary,
)
from ado2gh.api.pipeline_runner import (
    MIGRATE_UI_PIPELINE_STEPS,
    resolve_pipeline_step_defs,
)
from ado2gh.models import MigrationScope, RepoConfig


def _repo(project: str = "Proj", name: str = "app") -> RepoConfig:
    return RepoConfig(
        ado_project=project,
        ado_repo=name,
        gh_org="gh-org",
        gh_repo=name,
        phase="poc",
    )


def test_build_work_items_categories():
    items = build_work_items_for_repos(
        [_repo()],
        enabled_scopes=[
            MigrationScope.REPO.value,
            MigrationScope.PIPELINES.value,
            MigrationScope.SECRETS.value,
        ],
        db=None,
    )
    by_scope = {wi["scope"]: wi for wi in items}
    assert by_scope["repo"]["category"] == "migrate_repo"
    assert by_scope["pipelines"]["category"] == "convert_metadata"
    assert by_scope["secrets"]["category"] == "manual_setup"
    assert by_scope["pipelines"]["status"] == "blocked"
    assert "inventory" in (by_scope["pipelines"].get("blocker") or "").lower()


def test_build_work_items_pipeline_blockers_from_db():
    db = MagicMock()
    pipe = MagicMock()
    pipe.pipeline_name = "ci-build"
    pipe.service_connections = [{"name": "azure-sub"}]
    pipe.variable_groups = []
    db.get_pipelines_for_repo.return_value = [pipe]

    items = build_work_items_for_repos(
        [_repo()],
        enabled_scopes=[MigrationScope.PIPELINES.value, MigrationScope.SECRETS.value],
        db=db,
    )
    secrets = next(wi for wi in items if wi["scope"] == "secrets")
    assert secrets["status"] == "blocked"
    assert "azure-sub" in (secrets.get("blocker") or "")


def test_apply_scope_results_updates_work_items():
    work_items = build_work_items_for_repos(
        [_repo()],
        enabled_scopes=[MigrationScope.REPO.value],
        db=None,
    )
    repo_details = [{
        "repo": "Proj/app",
        "scopes": [{"scope": "repo", "status": "completed", "detail": "GEI ok"}],
    }]
    updated = apply_scope_results_to_work_items(work_items, repo_details)
    repo_item = next(wi for wi in updated if wi["scope"] == "repo")
    assert repo_item["status"] == "completed"
    assert repo_item["blocker"] == ""


def test_plan_narrative_lists_blocked_items():
    items = build_work_items_for_repos(
        [_repo()],
        enabled_scopes=[MigrationScope.REPO.value, MigrationScope.SECRETS.value],
        db=None,
    )
    text = plan_narrative_from_work_items("poc", items, dry_run=True)
    assert "poc" in text
    assert "dry-run" in text or "dry run" in text.lower()


def test_work_items_summary_counts():
    items = [
        {"status": "ready"},
        {"status": "blocked"},
        {"status": "skipped"},
    ]
    summary = work_items_summary(items)
    assert summary["ready"] == 1
    assert summary["blocked"] == 1
    assert summary["skipped"] == 1


def test_aggregate_work_items_for_timeline_counts_repos():
    items = build_work_items_for_repos(
        [
            RepoConfig("P1", "a", "g", "a"),
            RepoConfig("P1", "b", "g", "b"),
        ],
        enabled_scopes=["repo", "pipelines"],
        db=None,
    )
    rows = aggregate_work_items_for_timeline(items)
    repo_row = next(r for r in rows if r["scope"] == "repo")
    assert repo_row["count"] == 2
    assert "2 repos" in repo_row["label"]
    assert "P1/a" not in repo_row["label"]


def test_resolve_pipeline_step_defs_agent_defaults():
    ids = [s["id"] for s in MIGRATE_UI_PIPELINE_STEPS]
    resolved = resolve_pipeline_step_defs(ids)
    assert resolved[0]["id"] == "connect"
    assert any(s["id"] == "migrate_repos" for s in resolved)
    assert any(s["id"] == "convert_pipelines" for s in resolved)
    assert any(s["id"] == "analyze_deps" for s in resolved)


def test_filter_work_items_for_scopes():
    items = build_work_items_for_repos(
        [_repo()],
        enabled_scopes=[
            MigrationScope.REPO.value,
            MigrationScope.PIPELINES.value,
            MigrationScope.SECRETS.value,
        ],
        db=None,
    )
    repo_only = filter_work_items_for_scopes(items, ["repo"])
    assert len(repo_only) == 1
    assert repo_only[0]["scope"] == "repo"
    assert len(filter_work_items_for_scopes(items, None)) == len(items)
