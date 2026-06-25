"""Unit tests for agent pipeline plan alignment with Migrate tab."""
from ado2gh.agents.migration_agent.pipeline_plan import (
    AGENT_PIPELINE_STEP_IDS,
    finalize_agent_migration_plan,
    plan_confirmation_summary,
    pipeline_narrative_section,
    work_item_scopes,
)
from ado2gh.api.pipeline_models import MIGRATE_UI_PIPELINE_STEPS


def test_agent_pipeline_step_ids_match_migrate_tab():
    expected = [s["id"] for s in MIGRATE_UI_PIPELINE_STEPS]
    assert AGENT_PIPELINE_STEP_IDS == expected
    assert AGENT_PIPELINE_STEP_IDS == [
        "connect",
        "analyze_deps",
        "migrate_repos",
        "convert_pipelines",
        "validate",
    ]


def test_finalize_agent_migration_plan_attaches_pipeline_metadata():
    from ado2gh.api.migration_work_plan import build_work_items_for_repos
    from ado2gh.agents.migration_agent.pipeline_plan import repo_config_from_discovery

    session = {"plan_repository_id": "Proj/RepoA", "dry_run": True, "plan_phase": "poc"}
    repo_cfg = repo_config_from_discovery(
        {"project": "Proj", "repo_name": "RepoA", "name": "RepoA"},
        session,
    )
    work_items = build_work_items_for_repos(
        [repo_cfg],
        enabled_scopes=["repo", "pipelines", "secrets"],
        db=None,
    )
    plan = finalize_agent_migration_plan(
        {
            "repos": [{"id": "Proj/RepoA", "name": "RepoA"}],
            "work_items": work_items,
            "dry_run": True,
            "revision": 0,
            "repo_count": 1,
        },
        session,
    )
    assert plan["pipeline_step_ids"] == AGENT_PIPELINE_STEP_IDS
    assert len(plan["pipeline_steps"]) == 5
    assert plan["confirmation_summary"]
    assert "Secret mappings" in plan["confirmation_summary"]
    assert "Pipeline steps" not in plan["confirmation_summary"]
    assert plan["repository_id"] == "Proj/RepoA"


def test_plan_confirmation_summary_omits_phase_when_unspecified():
    session = {"plan_repository_id": "Proj/RepoA", "dry_run": True}
    plan = {
        "repos": [{"id": "Proj/RepoA", "gh_org": "my-org", "gh_repo": "RepoA"}],
        "repo_count": 1,
        "dry_run": True,
    }
    text = plan_confirmation_summary(plan, session)
    assert "Phase" not in text
    assert "`Proj/RepoA`" in text


def test_finalize_agent_migration_plan_strips_phase_assumptions_when_unspecified():
    from ado2gh.agents.migration_agent.pipeline_plan import finalize_agent_migration_plan

    session = {"plan_repository_id": "Proj/RepoA", "dry_run": True}
    plan = {
        "repos": [{"id": "Proj/RepoA"}],
        "work_items": [],
        "dry_run": True,
        "repo_count": 1,
        "assumptions": [
            "No inter-repo dependencies detected",
            "Phase: poc (proof of concept)",
            "Manual setup required for secrets/service connections",
        ],
    }
    finalized = finalize_agent_migration_plan(plan, session)
    assert "phase" not in finalized
    joined = " ".join(finalized.get("assumptions", []))
    assert "poc" not in joined.lower()
    assert "inter-repo" in joined
    assert "Manual setup" in joined


def test_plan_confirmation_summary_single_repo():
    session = {"plan_repository_id": "Proj/RepoA", "dry_run": True}
    plan = {
        "repos": [{"id": "Proj/RepoA", "gh_org": "my-org", "gh_repo": "RepoA"}],
        "repo_count": 1,
        "dry_run": True,
    }
    text = plan_confirmation_summary(plan, session)
    assert "`Proj/RepoA`" in text
    assert "`my-org/RepoA`" in text
    assert "Manual setup" not in text
    assert "Blocked" not in text


def test_plan_confirmation_summary_bulk_caps_repo_list():
    session = {"dry_run": True, "plan_phase": "wave1"}
    repos = [{"id": f"P/r{i}"} for i in range(10)]
    plan = {"repos": repos, "repo_count": 10, "phase": "wave1", "dry_run": True}
    text = plan_confirmation_summary(plan, session)
    assert "10 repositories" in text
    assert "P/r0" in text
    assert "and 7 more" in text
    assert "P/r9" not in text


def test_pipeline_narrative_section_lists_all_steps():
    text = pipeline_narrative_section()
    assert "Load discovery data" in text
    assert "Analyze dependencies" in text
    assert "Migrate repositories" in text
    assert "Convert workflows" in text
    assert "Validate" in text


def test_work_item_scopes_supports_legacy_and_per_scope():
    assert work_item_scopes({"scope": "repo"}) == ["repo"]
    assert work_item_scopes({"scopes": ["git", "pipelines"]}) == ["git", "pipelines"]
    assert work_item_scopes({}) == ["repo"]
