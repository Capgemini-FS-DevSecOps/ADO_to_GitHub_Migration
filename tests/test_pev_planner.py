from __future__ import annotations

import copy

import pytest

from ado2gh.models import RepoConfig
from ado2gh.pev.contracts import MigrationPlan, PlanIntegrityError, content_digest
from ado2gh.pev.executor import PEVExecutor
from ado2gh.pev.planner import MigrationPlanner, _non_secret_config


class FakeADO:
    org_url = "https://dev.azure.com/example"

    def list_projects(self):
        return [{"name": "Payments"}, {"name": "Store"}]

    def list_repos(self, project):
        return [{
            "id": f"id-{project}",
            "name": "api",
            "defaultBranch": "refs/heads/main",
            "isDisabled": False,
        }]

    def get_repo(self, project, repo):
        repo_id = repo if str(repo).startswith("id-") else f"id-{project}-{repo}"
        branch = "trunk" if str(repo_id).endswith("-service") else "main"
        return {
            "id": repo_id,
            "name": repo,
            "defaultBranch": f"refs/heads/{branch}",
        }

    def get_repo_commits(self, project, repo_id, top=1, branch=""):
        return [{"commitId": "a" * 40}]

    def list_refs(self, _project, repo_id, prefix):
        branch = "trunk" if str(repo_id).endswith("-service") else "main"
        if prefix == "heads/":
            return [{"name": f"refs/heads/{branch}", "objectId": "a" * 40}]
        if prefix == "tags/":
            return [{"name": "refs/tags/v1", "objectId": "b" * 40}]
        raise AssertionError(prefix)

    def list_variable_groups(self, project):
        return []

    def list_service_connections(self, project):
        return []

    def list_all_pipelines(self, project):
        return iter(())

    def list_all_release_pipelines(self, project):
        return iter(())


class FakeGH:
    def __init__(self, existing=()):
        self.existing = {item.casefold() for item in existing}

    def repo_exists(self, org, repo):
        return f"{org}/{repo}".casefold() in self.existing


def config(**mapping):
    return {
        "ado_org_url": "https://dev.azure.com/example",
        "gh_org": "target",
        "default_scopes": ["repo", "pipelines"],
        "mapping": {"strategy": "project-prefix", **mapping},
    }


def test_org_plan_is_collision_free_and_content_addressed():
    plan = MigrationPlanner(FakeADO(), config(), FakeGH()).create_plan()

    assert [repo.gh_repo for repo in plan.repositories] == [
        "payments-api", "store-api"
    ]
    assert plan.plan_id.startswith("plan_")
    assert any(task.execution_mode == "hybrid" for task in plan.tasks)
    assert plan.repositories[0].source_branch_refs == (
        ("refs/heads/main", "a" * 40),
    )
    assert plan.repositories[0].source_tag_refs == (
        ("refs/tags/v1", "b" * 40),
    )

    loaded = MigrationPlan.from_dict(plan.to_dict())
    assert loaded.plan_id == plan.plan_id


def test_preserve_strategy_blocks_case_insensitive_collision():
    with pytest.raises(PlanIntegrityError, match="collision"):
        MigrationPlanner(
            FakeADO(), config(strategy="preserve"), FakeGH()
        ).create_plan()


def test_existing_target_requires_explicit_reuse_policy():
    with pytest.raises(PlanIntegrityError, match="already exist"):
        MigrationPlanner(
            FakeADO(), config(), FakeGH(["target/payments-api"])
        ).create_plan()


def test_plan_tampering_is_detected():
    plan = MigrationPlanner(FakeADO(), config(), FakeGH()).create_plan()
    payload = copy.deepcopy(plan.to_dict())
    payload["repositories"][0]["gh_repo"] = "changed-after-approval"

    with pytest.raises(PlanIntegrityError, match="digest mismatch"):
        MigrationPlan.from_dict(payload)


def test_secret_values_do_not_change_approved_plan_identity():
    first_cfg = {**config(), "ado_pat": "secret-one", "gh_token": "token-one"}
    second_cfg = {**config(), "ado_pat": "secret-two", "gh_token": "token-two"}

    first = MigrationPlanner(FakeADO(), first_cfg, FakeGH()).create_plan()
    second = MigrationPlanner(FakeADO(), second_cfg, FakeGH()).create_plan()

    assert first.config_digest == second.config_digest
    assert first.plan_id == second.plan_id


def test_programmatic_config_normalizes_llm_identity_for_plan_and_executor():
    cfg = {
        **config(),
        "pipeline_conversion": {
            "llm_provider": "openai",
            "llm_model": "approved-model",
            "llm_base_url": "https://llm.example/v1/",
        },
    }
    plan = MigrationPlanner(FakeADO(), cfg, FakeGH()).create_plan()
    policy = plan.policy["pipeline_conversion"]

    assert policy["llm_provider"] == "openai-responses"
    assert policy["llm_model"] == "approved-model"
    assert policy["llm_base_url"] == "https://llm.example/v1"
    assert policy["llm_organization"] == ""
    assert policy["llm_project"] == ""
    assert policy["llm_api_key_env"] == "OPENAI_API_KEY"

    executor = PEVExecutor(cfg, FakeADO(), FakeGH(), object())
    assert content_digest(_non_secret_config(executor.cfg)) == plan.config_digest


def test_explicit_repository_mapping_is_preserved():
    repo = RepoConfig(
        ado_project="Payments",
        ado_repo="service",
        gh_org="special-org",
        gh_repo="service-v2",
        scopes=["repo"],
    )
    plan = MigrationPlanner(FakeADO(), config(), FakeGH()).create_plan([repo])

    assert plan.repositories[0].target_key == "special-org/service-v2"
    assert plan.repositories[0].default_branch == "trunk"
