from ado2gh.agents.migration_agent.utils import canonical_repo_id, normalize_repo_key


def test_normalize_repo_key_strips_leading_slash():
    assert normalize_repo_key("/azure-pipelines/app") == "azure-pipelines/app"
    assert normalize_repo_key("  /Proj/Repo  ") == "Proj/Repo"


def test_canonical_repo_id_normalizes_id_field():
    repo = {"id": "/azure-pipelines/app", "project": "azure-pipelines", "repo_name": "app"}
    assert canonical_repo_id(repo) == "azure-pipelines/app"
