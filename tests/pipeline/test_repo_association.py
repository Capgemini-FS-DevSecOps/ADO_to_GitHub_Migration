"""Tests for pipeline-to-repo association heuristics."""
from ado2gh.pipelines.repo_association import infer_pipeline_repo_name, pipeline_belongs_to_repo


def test_infer_pipeline_repo_name_exact_match():
    repos = [{"name": "azure-pipelines"}, {"name": "other-repo"}]
    assert infer_pipeline_repo_name("azure-pipelines", "", "", repos) == "azure-pipelines"


def test_infer_pipeline_repo_name_prefix_variant():
    repos = [{"name": "azure-pipelines"}]
    assert (
        infer_pipeline_repo_name("azure-pipelines-migration", "", "", repos)
        == "azure-pipelines"
    )
    assert (
        infer_pipeline_repo_name("azure-pipelines-script-migration", "", "", repos)
        == "azure-pipelines"
    )


def test_pipeline_belongs_to_repo_uses_prefix_when_repo_name_missing():
    assert pipeline_belongs_to_repo(
        "azure-pipelines-migration",
        "azure-pipelines",
        "",
    )


def test_summarize_project_inventory_totals():
    from ado2gh.pipelines.inventory import summarize_project_inventory

    summary = {
        "ProjA": {"build": 2, "release": 1, "total": 3},
        "ProjB": {"build": 4, "release": 0, "total": 4},
    }
    totals = summarize_project_inventory(summary)
    assert totals["pipelines"] == 7
    assert totals["build"] == 6
    assert totals["release"] == 1
