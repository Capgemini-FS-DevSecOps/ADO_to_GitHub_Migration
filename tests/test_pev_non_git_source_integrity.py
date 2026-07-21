from __future__ import annotations

import json

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.contracts import compute_source_refs_digest
from ado2gh.pev.executor import PEVExecutor
from ado2gh.pev.planner import MigrationPlanner
from ado2gh.pev.source_integrity import create_scope_snapshot
from ado2gh.state.db import StateDB


class NonGitADO:
    org_url = "https://dev.azure.com/example"

    def __init__(self):
        self.work_item_title = "Initial title"
        self.work_item_calls = 0
        self.policy_calls = 0
        self.policies = []

    def get_repo(self, _project, repo):
        return {
            "id": "repo-id",
            "name": repo,
            "defaultBranch": "refs/heads/main",
        }

    def list_refs(self, _project, _repo_id, prefix):
        if prefix == "heads/":
            return [{"name": "refs/heads/main", "objectId": "a" * 40}]
        if prefix == "tags/":
            return []
        raise AssertionError(prefix)

    def get_repo_stats(self, _project, _repo_id):
        return {"branch_count": 1}

    def list_work_items(self, _project, **_kwargs):
        self.work_item_calls += 1
        return [{
            "id": 7,
            "fields": {
                "System.Title": self.work_item_title,
                "System.WorkItemType": "Bug",
                "System.State": "Active",
                "System.Description": "private-work-item-content",
            },
        }]

    def list_wiki_pages(self, _project):
        return [{
            "wiki": {"id": "wiki-id", "name": "Engineering"},
            "root": {
                "path": "/Home",
                "content": "private-wiki-content",
                "subPages": [],
            },
        }]

    def list_variable_groups(self, _project):
        return [{
            "id": 1,
            "name": "production",
            "type": "Vsts",
            "variables": {
                "PASSWORD": {"isSecret": True, "value": "plaintext-secret"},
            },
        }]

    def list_service_connections(self, _project):
        return [{"id": "sc-1", "name": "azure-prod", "type": "azurerm"}]

    def list_branch_policies(self, _project, _repo_id):
        self.policy_calls += 1
        return self.policies


class MutableGH:
    def __init__(self, *, exists=False, repo_id="target-a", size=0):
        self.exists = exists
        self.repo_id = repo_id
        self.size = size
        self.default_branch = ""
        self.branch_refs = {}
        self.tag_refs = {}
        self.protections = []
        self.branch_protections = {}

    def repo_exists(self, _org, _repo):
        return self.exists

    def get_repo(self, _org, _repo):
        return {
            "node_id": self.repo_id,
            "size": self.size,
            "default_branch": self.default_branch,
        }

    def list_git_refs(self, _org, _repo, namespace):
        refs = self.branch_refs if namespace == "heads" else self.tag_refs
        return [
            {"ref": name, "object": {"sha": sha, "type": "commit"}}
            for name, sha in sorted(refs.items())
        ]

    def set_branch_protection(self, org, repo, branch, required_reviewers=1):
        self.protections.append((org, repo, branch, required_reviewers))
        value = {
            "required_pull_request_reviews": {
                "required_approving_review_count": required_reviewers,
                "dismiss_stale_reviews": True,
            },
        }
        self.branch_protections[branch] = value
        return value

    def get_branch_protection(self, _org, _repo, branch):
        return self.branch_protections.get(branch)


def _repo(scopes):
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=list(scopes),
    )


def _cfg(scopes, **mapping):
    return {
        "ado_org_url": "https://dev.azure.com/example",
        "gh_org": "octo",
        "default_scopes": list(scopes),
        "parallel": 1,
        "mapping": {
            "strategy": "project-prefix",
            "existing_target_policy": "fail",
            **mapping,
        },
    }


def _source_ref_cfg():
    return {
        "Payments/api": {
            "source_repo_id": "repo-id",
            "default_branch": "main",
            "source_head_sha": "a" * 40,
            "source_branch_refs": {"refs/heads/main": "a" * 40},
            "source_tag_refs": {},
            "source_refs_digest": "unused-by-non-git-scopes",
        },
    }


def test_planner_binds_all_non_git_scopes_without_storing_payloads():
    ado = NonGitADO()
    scopes = [
        MigrationScope.WORK_ITEMS.value,
        MigrationScope.WIKI.value,
        MigrationScope.SECRETS.value,
        MigrationScope.BRANCH_POLICIES.value,
    ]
    plan = MigrationPlanner(ado, _cfg(scopes), MutableGH()).create_plan(
        [_repo(scopes)]
    )

    execute_tasks = {
        task.scope: task for task in plan.tasks if task.kind == "execute"
    }
    assert set(execute_tasks) == set(scopes)
    assert execute_tasks[MigrationScope.WORK_ITEMS.value].metadata[
        "source_snapshot"
    ]["item_count"] == 1
    assert execute_tasks[MigrationScope.WIKI.value].metadata[
        "source_snapshot"
    ]["counts"] == {"item_count": 1, "wiki_count": 1}
    assert execute_tasks[MigrationScope.SECRETS.value].metadata[
        "source_snapshot"
    ]["counts"]["item_count"] == 2

    serialized = json.dumps(plan.to_dict())
    assert "private-work-item-content" not in serialized
    assert "private-wiki-content" not in serialized
    assert "plaintext-secret" not in serialized


def test_executor_fetches_once_and_blocks_work_item_drift_before_transform():
    ado = NonGitADO()
    original = ado.list_work_items("Payments")
    approved = create_scope_snapshot(MigrationScope.WORK_ITEMS.value, original)
    ado.work_item_calls = 0
    ado.work_item_title = "Changed after approval"
    cfg = {
        "pev_plan_id": "plan-approved",
        "pev_source_ref_snapshots": _source_ref_cfg(),
        "pev_non_git_source_snapshots": {
            "Payments/api": {MigrationScope.WORK_ITEMS.value: approved},
        },
    }
    engine = MigrationEngine(
        cfg, ado, MutableGH(), StateDB(":memory:"), dry_run=True
    )

    result = engine.migrate_repo(
        1, _repo([MigrationScope.WORK_ITEMS.value])
    )

    assert ado.work_item_calls == 1
    assert result["scopes"][MigrationScope.WORK_ITEMS.value]["status"] == "failed"
    assert "source drift" in result["scopes"][
        MigrationScope.WORK_ITEMS.value
    ]["error"]


def test_lossy_and_unsupported_branch_policies_require_review_with_evidence():
    ado = NonGitADO()
    ado.policies = [
        {
            "id": 10,
            "type": {
                "id": MigrationEngine.ADO_MINIMUM_REVIEWERS_POLICY,
                "displayName": "Minimum reviewers",
            },
            "isEnabled": True,
            "isBlocking": True,
            "settings": {
                "minimumApproverCount": 2,
                "creatorVoteCounts": True,
                "resetOnSourcePush": False,
                "scope": [{
                    "repositoryId": "repo-id",
                    "refName": "refs/heads/main",
                    "matchKind": "Exact",
                }],
            },
        },
        {
            "id": 11,
            "type": {"id": "build-policy", "displayName": "Build validation"},
            "isEnabled": True,
            "isBlocking": True,
            "settings": {"scope": [{
                "repositoryId": "repo-id",
                "refName": "refs/heads/release",
                "matchKind": "Exact",
            }]},
        },
    ]
    approved = create_scope_snapshot(
        MigrationScope.BRANCH_POLICIES.value, ado.policies
    )
    cfg = {
        "pev_plan_id": "plan-approved",
        "pev_source_ref_snapshots": _source_ref_cfg(),
        "pev_non_git_source_snapshots": {
            "Payments/api": {MigrationScope.BRANCH_POLICIES.value: approved},
        },
    }
    gh = MutableGH()
    engine = MigrationEngine(cfg, ado, gh, StateDB(":memory:"), dry_run=False)

    result = engine.migrate_repo(
        1, _repo([MigrationScope.BRANCH_POLICIES.value])
    )

    scope_result = result["scopes"][MigrationScope.BRANCH_POLICIES.value]
    assert ado.policy_calls == 1
    assert scope_result["status"] == "needs_review"
    assert scope_result["detail"]["affected_branches"] == ["main", "release"]
    assert scope_result["detail"]["unsupported_policies"] == 1
    assert scope_result["detail"]["lossy_policies"] >= 1
    assert gh.protections == [("octo", "payments-api", "main", 2)]
    assert {item["outcome"] for item in scope_result["detail"]["policy_evidence"]} \
        == {"converted_with_review", "unsupported"}


class ReceiptEngine:
    calls = 0

    def __init__(self, cfg, ado, gh, db, **_kwargs):
        self.cfg = cfg
        self.ado = ado
        self.gh = gh
        self.db = db

    def migrate_repo(self, _wave_id, repo, **_kwargs):
        type(self).calls += 1
        if type(self).calls == 1:
            self.gh.exists = True
            self.db.record_repository_ownership(
                source_org=self.ado.org_url,
                ado_project=repo.ado_project,
                ado_repo=repo.ado_repo,
                gh_org=repo.gh_org,
                gh_repo=repo.gh_repo,
                plan_id=self.cfg["pev_plan_id"],
                run_id=self.cfg["pev_run_id"],
                target_repo_id=self.gh.repo_id,
                status="created",
            )
            return {
                "status": "failed",
                "scopes": {
                    MigrationScope.REPO.value: {
                        "status": "failed",
                        "error": "simulated crash",
                    },
                },
            }
        return {
            "status": "completed",
            "scopes": {
                MigrationScope.REPO.value: {
                    "status": "completed",
                    "detail": {"resumed": True},
                },
            },
        }


def test_normal_resume_accepts_only_exact_current_run_target_ownership(tmp_path):
    ReceiptEngine.calls = 0
    ado = NonGitADO()
    gh = MutableGH(exists=False, repo_id="created-id", size=0)
    db = StateDB(str(tmp_path / "state.db"))
    cfg = _cfg([MigrationScope.REPO.value])
    plan = MigrationPlanner(ado, cfg, gh, db).create_plan(
        [_repo([MigrationScope.REPO.value])]
    )
    executor = PEVExecutor(cfg, ado, gh, db, engine_factory=ReceiptEngine)

    first = executor.execute(plan, approved_plan_id=plan.plan_id)
    assert first.status == "failed"
    resumed = executor.execute(
        plan, approved_plan_id=plan.plan_id, run_id=first.run_id
    )

    assert resumed.status == "executed"
    assert ReceiptEngine.calls == 2


def test_reused_target_replacement_is_rejected_before_execution(tmp_path):
    ReceiptEngine.calls = 99
    ado = NonGitADO()
    gh = MutableGH(exists=True, repo_id="approved-id", size=0)
    db = StateDB(str(tmp_path / "state.db"))
    cfg = _cfg(
        [MigrationScope.REPO.value], existing_target_policy="reuse"
    )
    plan = MigrationPlanner(ado, cfg, gh, db).create_plan(
        [_repo([MigrationScope.REPO.value])]
    )
    gh.repo_id = "replacement-id"

    result = PEVExecutor(
        cfg, ado, gh, db, engine_factory=ReceiptEngine
    ).execute(plan, approved_plan_id=plan.plan_id)

    assert result.status == "failed"
    assert "identity drift" in result.repository_results["Payments/api"][
        "errors"
    ][0]
    assert ReceiptEngine.calls == 99


def test_reused_target_same_id_ref_drift_is_rejected_before_execution(tmp_path):
    ReceiptEngine.calls = 99
    ado = NonGitADO()
    gh = MutableGH(exists=True, repo_id="approved-id", size=1)
    gh.default_branch = "main"
    gh.branch_refs = {"refs/heads/main": "c" * 40}
    db = StateDB(str(tmp_path / "state.db"))
    cfg = _cfg(
        [MigrationScope.REPO.value],
        existing_target_policy="reuse",
        allow_nonempty_target=True,
    )
    plan = MigrationPlanner(ado, cfg, gh, db).create_plan(
        [_repo([MigrationScope.REPO.value])]
    )
    gh.branch_refs["refs/heads/main"] = "d" * 40

    result = PEVExecutor(
        cfg, ado, gh, db, engine_factory=ReceiptEngine
    ).execute(plan, approved_plan_id=plan.plan_id)

    assert result.status == "failed"
    assert "Target ref drift" in result.repository_results["Payments/api"][
        "errors"
    ][0]
    assert ReceiptEngine.calls == 99


def test_migration_engine_rechecks_approved_target_refs_immediately_before_write():
    ado = NonGitADO()
    gh = MutableGH(exists=True, repo_id="approved-id", size=1)
    gh.default_branch = "main"
    gh.branch_refs = {"refs/heads/main": "d" * 40}
    approved_branches = {"refs/heads/main": "c" * 40}
    source_branches = {"refs/heads/main": "a" * 40}
    cfg = {
        "pev_plan_id": "plan-approved",
        "pev_run_id": "run-approved",
        "mapping": {
            "existing_target_policy": "reuse",
            "allow_nonempty_target": True,
        },
        "pev_source_ref_snapshots": {
            "Payments/api": {
                "source_repo_id": "repo-id",
                "default_branch": "main",
                "source_head_sha": "a" * 40,
                "source_branch_refs": source_branches,
                "source_tag_refs": {},
                "source_refs_digest": compute_source_refs_digest(
                    tuple(source_branches.items()), ()
                ),
            },
        },
        "pev_target_snapshots": {
            "Payments/api": {
                "target_exists": True,
                "target_repo_id": "approved-id",
                "target_size": 1,
                "target_default_branch": "main",
                "target_branch_refs": approved_branches,
                "target_tag_refs": {},
                "target_refs_digest": compute_source_refs_digest(
                    tuple(approved_branches.items()), ()
                ),
            },
        },
    }
    engine = MigrationEngine(
        cfg, ado, gh, StateDB(":memory:"), dry_run=True
    )

    result = engine.migrate_repo(1, _repo([MigrationScope.REPO.value]))

    assert result["scopes"][MigrationScope.REPO.value]["status"] == "failed"
    assert "GitHub target drift" in result["scopes"][
        MigrationScope.REPO.value
    ]["error"]
