"""Unit tests for discovery-backed repository validation helpers."""
from ado2gh.agents.migration_agent.utils import (
    canonical_repo_id,
    find_discovery_repo,
    normalize_discovery_repo,
    validate_repo_against_discovery,
)


DISCOVERY = {
    "repos": [
        {
            "project": "azure-pipelines",
            "repo_name": "bicep-template-migration",
            "name": "bicep-template-migration",
        }
    ],
    "repos_scanned": 1,
}


def test_canonical_repo_id_from_discovery_repo():
    repo = DISCOVERY["repos"][0]
    assert canonical_repo_id(repo) == "azure-pipelines/bicep-template-migration"


def test_find_discovery_repo_rejects_partial_name():
    assert find_discovery_repo("bicep-template", DISCOVERY["repos"]) is None


def test_find_discovery_repo_accepts_canonical_name():
    match = find_discovery_repo("azure-pipelines/bicep-template-migration", DISCOVERY["repos"])
    assert match is not None
    assert canonical_repo_id(match) == "azure-pipelines/bicep-template-migration"


def test_validate_repo_against_discovery_suggests_close_match():
    error = validate_repo_against_discovery("bicep-template", DISCOVERY)
    assert error is not None
    assert "Did you mean" in error
    assert "bicep-template-migration" in error


def test_normalize_discovery_repo_sets_id():
    normalized = normalize_discovery_repo(DISCOVERY["repos"][0])
    assert normalized["id"] == "azure-pipelines/bicep-template-migration"
