"""Unit tests for migrate_repos dry-run warning generation."""
from unittest.mock import MagicMock

from ado2gh.api.pipeline_models import PipelineRun, PipelineStep, StepStatus
from ado2gh.api.pipeline_runner import PipelineRunner
from ado2gh.models import RepoConfig


def _runner() -> PipelineRunner:
    settings = MagicMock()
    settings.load.return_value.advanced = MagicMock(
        config_path="migration.yaml",
        db_path="migration_state.db",
    )
    return PipelineRunner(settings=settings)


def test_repo_migration_dry_run_warnings_include_size_and_upstream():
    pr = _runner()
    run = PipelineRun(
        id="r1",
        name="test",
        repository_id="Proj/app",
        steps=[
            PipelineStep(
                id="analyze_deps",
                label="Analyze dependencies",
                description="",
                status=StepStatus.COMPLETED,
                result={
                    "migration_order": ["Proj/dep", "Proj/app"],
                    "warnings": [],
                },
            ),
        ],
    )
    repos = [
        RepoConfig(
            ado_project="Proj",
            ado_repo="app",
            gh_org="gh",
            gh_repo="app",
        ),
    ]
    ado = MagicMock()
    ado.get_repo.return_value = {"id": "uuid-1", "size": 12 * 1024}
    ado.get_repo_stats.return_value = {"branch_count": 3}
    ado.list_wiki_pages.return_value = []
    ado.list_artifacts.return_value = []
    ado.list_work_items.return_value = []
    ado.list_branch_policies.return_value = []

    warnings = pr._repo_migration_dry_run_warnings(run, repos, ado=ado, db=None)

    assert any("Upstream repos to migrate first" in w and "Proj/dep" in w for w in warnings)
    assert any("Repository size" in w for w in warnings)
    assert not any("service connection" in w.lower() for w in warnings)


def test_pipeline_conversion_dry_run_warnings_lists_service_connections():
    pr = _runner()
    run = PipelineRun(
        id="r1",
        name="test",
        repository_id="Proj/app",
        steps=[
            PipelineStep(
                id="analyze_deps",
                label="Analyze dependencies",
                description="",
                status=StepStatus.COMPLETED,
                result={
                    "dependencies": {
                        "Proj/app": {
                            "service_connections": [
                                {"name": "sp-sreassets-owner"},
                                {"name": "${{parameters.azureServicePrincipalName}}"},
                            ],
                        },
                    },
                },
            ),
        ],
    )
    repos = [
        RepoConfig(
            ado_project="Proj",
            ado_repo="app",
            gh_org="gh",
            gh_repo="app",
        ),
    ]

    warnings = pr._pipeline_conversion_dry_run_warnings(run, repos)

    assert len(warnings) == 2
    assert any("sp-sreassets-owner" in w for w in warnings)
    assert any("azureServicePrincipalName" in w for w in warnings)
