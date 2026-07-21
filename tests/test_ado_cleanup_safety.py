from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from ado2gh.core.ado_cleanup import (
    ADOCleanup,
    build_cleanup_destructive_request,
)
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.contracts import (
    MigrationPlan,
    PlanTask,
    PlannedRepository,
    compute_source_refs_digest,
    content_digest,
)
from ado2gh.state.db import StateDB


SOURCE_SHA = "a" * 40
TAG_SHA = "b" * 40


def _receipt(pipeline_id: int, name: str, revision: int = 3) -> dict:
    return {
        "pipeline_id": pipeline_id,
        "pipeline_name": name,
        "pipeline_type": "yaml",
        "repo_id": "repo-id",
        "repo_name": "api",
        "source_yaml_path": f"pipelines/{pipeline_id}.yml",
        "source_yaml_sha256": f"{pipeline_id:064x}"[-64:],
        "source_revision": revision,
        "metadata_schema_version": 2,
        "inventory_ruleset_version": "test/v2",
        "semantic_metadata_sha256": f"{pipeline_id + 100:064x}"[-64:],
    }


def _plan(
    *, pipeline_filter: str = "", receipts=(),
    target_api_url: str = "https://api.github.com",
) -> MigrationPlan:
    source, target = "Payments/api", "octo/payments-api"
    preflight = PlanTask(
        "preflight", "preflight", source, target,
        metadata={
            "target_exists": False,
            "target_repo_id": "",
            "target_size": 0,
            "target_default_branch": "",
            "target_branch_refs": {},
            "target_tag_refs": {},
            "target_refs_digest": compute_source_refs_digest((), ()),
        },
    )
    inventory = PlanTask(
        "inventory", "inventory", source, target,
        scope=MigrationScope.PIPELINES.value,
        dependencies=(preflight.task_id,),
        metadata={
            "pipelines": list(receipts),
            "pipeline_count": len(receipts),
            "inventory_digest": content_digest(list(receipts)),
        },
    )
    execute_repo = PlanTask(
        "execute-repo", "execute", source, target,
        scope=MigrationScope.REPO.value,
        dependencies=(preflight.task_id,),
    )
    execute_pipelines = PlanTask(
        "execute-pipelines", "execute", source, target,
        scope=MigrationScope.PIPELINES.value,
        dependencies=(preflight.task_id, inventory.task_id, execute_repo.task_id),
    )
    validate = PlanTask(
        "validate", "validate", source, target,
        dependencies=(execute_repo.task_id, execute_pipelines.task_id),
    )
    branches = (("refs/heads/main", SOURCE_SHA),)
    tags = (("refs/tags/v1", TAG_SHA),)
    return MigrationPlan.create(
        source_org_url="https://dev.azure.com/example",
        target_org="octo",
        repositories=(PlannedRepository(
            ado_project="Payments",
            ado_repo="api",
            gh_org="octo",
            gh_repo="payments-api",
            scopes=(MigrationScope.REPO.value, MigrationScope.PIPELINES.value),
            source_repo_id="repo-id",
            default_branch="main",
            source_head_sha=SOURCE_SHA,
            source_branch_refs=branches,
            source_tag_refs=tags,
            source_refs_digest=compute_source_refs_digest(branches, tags),
            pipeline_filter=pipeline_filter,
            archive_source=True,
        ),),
        tasks=(preflight, inventory, execute_repo, execute_pipelines, validate),
        policy={
            "mapping": {"existing_target_policy": "fail"},
            "cleanup": {
                "allow_disable_pipelines": True,
                "allow_redirect": True,
                "allow_archive": True,
            },
            "runtime_context": {
                "source_api_url": "https://dev.azure.com/example",
                "source_org_identity": {
                    "kind": "logical", "id": "logical:source-org",
                },
                "target_api_url": target_api_url,
                "target_org_identities": {
                    "octo": {"kind": "logical", "id": "logical:target-org"},
                },
            },
        },
        config_digest=content_digest({"config": "approved"}),
    )


def _repo(plan: MigrationPlan) -> RepoConfig:
    return plan.repositories[0].to_repo_config()


def _register_capability_manifest(db: StateDB, plan: MigrationPlan) -> None:
    planned = plan.repositories[0]
    db.register_pev_plan_capabilities(plan.plan_id, [
        {
            "source_key": task.source_key,
            "gh_org": planned.gh_org,
            "gh_repo": planned.gh_repo,
            "scope": task.scope,
            "input_digest": content_digest(task.to_dict()),
        }
        for task in plan.tasks if task.kind == "execute"
    ])


def test_cleanup_destructive_capability_is_exact_one_shot_and_receipted():
    plan = _plan()
    db = StateDB(":memory:")
    run_id = "run-cleanup"
    db.upsert_pev_run(
        run_id, plan.plan_id, status="completed", config_digest=plan.config_digest
    )
    _register_capability_manifest(db, plan)
    request = build_cleanup_destructive_request(
        plan,
        run_id,
        [_repo(plan)],
        disable_pipelines=False,
        add_redirect=False,
        archive_repo=True,
        target_web_url="https://github.com",
    )
    capability = db.authorize_pev_destructive_capability(
        plan.plan_id,
        run_id,
        "ado_cleanup",
        request,
        approval={"ticket": "CHG-42", "actor": "operator"},
    )
    with pytest.raises(PermissionError, match="exact request"):
        db.claim_pev_destructive_capability(
            capability,
            plan.plan_id,
            run_id,
            "ado_cleanup",
            {**request, "target_web_url": "https://evil.example"},
            "worker-a",
        )
    token = db.claim_pev_destructive_capability(
        capability, plan.plan_id, run_id, "ado_cleanup", request, "worker-a"
    )
    with pytest.raises(RuntimeError, match="already claimed"):
        db.claim_pev_destructive_capability(
            capability, plan.plan_id, run_id, "ado_cleanup", request, "worker-b"
        )
    receipt = db.begin_pev_destructive_action(
        capability,
        plan_id=plan.plan_id,
        run_id=run_id,
        operation_kind="ado_cleanup",
        claimant="worker-a",
        claim_token=token,
        action_key="Payments/api:archive",
        source_key="Payments/api",
        target_key="octo/payments-api",
        action_kind="disable_ado_repository",
        before={"is_disabled": False},
    )
    assert db.finish_pev_destructive_action(
        receipt,
        capability_id=capability,
        claimant="worker-a",
        claim_token=token,
        status="completed",
        after={"is_disabled": True},
    )
    assert db.finish_pev_destructive_capability(
        capability,
        plan_id=plan.plan_id,
        run_id=run_id,
        operation_kind="ado_cleanup",
        claimant="worker-a",
        claim_token=token,
        status="completed",
    )
    [action] = db.list_pev_destructive_actions(capability)
    assert action["before_digest"] and action["after_digest"]
    assert action["status"] == "completed"


def test_redirect_requires_archive_and_binds_filtered_inventory_and_web_base():
    receipts = (_receipt(1, "deploy-api"), _receipt(2, "nightly"))
    plan = _plan(pipeline_filter="^deploy", receipts=receipts)
    repo = _repo(plan)
    with pytest.raises(ValueError, match="requires source archival"):
        build_cleanup_destructive_request(
            plan,
            "run-1",
            [repo],
            disable_pipelines=True,
            add_redirect=True,
            archive_repo=False,
            migration_date="2026-07-21",
            target_web_url="https://github.com",
        )
    request = build_cleanup_destructive_request(
        plan,
        "run-1",
        [repo],
        disable_pipelines=True,
        add_redirect=True,
        archive_repo=True,
        migration_date="2026-07-21",
        target_web_url="https://github.com",
    )
    [item] = request["repositories"]
    assert item["selected_pipeline_count"] == 1
    assert item["redirect_notice_sha256"]
    assert request["target_web_url"] == "https://github.com"


def test_enterprise_redirect_cannot_escape_plan_bound_github_authority():
    plan = _plan(target_api_url="https://ghe.example.test/api/v3")
    repo = _repo(plan)
    with pytest.raises(ValueError, match="inconsistent"):
        build_cleanup_destructive_request(
            plan,
            "run-1",
            [repo],
            disable_pipelines=False,
            add_redirect=True,
            archive_repo=True,
            migration_date="2026-07-21",
            target_web_url="https://github.com",
        )
    request = build_cleanup_destructive_request(
        plan,
        "run-1",
        [repo],
        disable_pipelines=False,
        add_redirect=True,
        archive_repo=True,
        migration_date="2026-07-21",
        target_web_url="https://ghe.example.test",
    )
    assert request["target_web_url"] == "https://ghe.example.test"


class _Response:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return deepcopy(self._payload)


class _Session:
    def __init__(self, ado):
        self.ado = ado
        self.puts = []
        self.posts = []
        self.patches = []

    def put(self, url, *, json, timeout):
        self.puts.append((url, deepcopy(json)))
        pipeline_id = int(json["id"])
        updated = deepcopy(json)
        updated["revision"] += 1
        self.ado.definitions[pipeline_id] = updated
        response_revision = updated["revision"]
        if self.ado.mismatch_response_revision_on_put == len(self.puts):
            response_revision += 100
        return _Response({"id": pipeline_id, "revision": response_revision})

    def post(self, url, *, json, timeout):
        self.posts.append((url, deepcopy(json)))
        if isinstance(json, list):
            [update] = json
            self.ado.branches[0]["objectId"] = update["newObjectId"]
            return _Response({"value": [{
                "name": update["name"],
                "oldObjectId": update["oldObjectId"],
                "newObjectId": update["newObjectId"],
                "success": True,
            }]})
        commit = "d" * 40
        self.ado.branches[0]["objectId"] = commit
        return _Response({
            "commits": [{"commitId": commit}],
            "refUpdates": [{"newObjectId": commit}],
        })

    def patch(self, url, *, json, timeout):
        self.patches.append((url, deepcopy(json)))
        self.ado.repo["isDisabled"] = bool(json["isDisabled"])
        if self.ado.drift_on_freeze and json["isDisabled"]:
            self.ado.branches[0]["objectId"] = "e" * 40
        return _Response({"isDisabled": self.ado.repo["isDisabled"]})


class _ADO:
    API = "api-version=7.1"
    API_RELEASE = "api-version=7.1"
    org_url = "https://dev.azure.com/example"

    def __init__(self, definitions=()):
        self.repo = {
            "id": "repo-id", "name": "api",
            "defaultBranch": "refs/heads/main", "isDisabled": False,
        }
        self.branches = [{"name": "refs/heads/main", "objectId": SOURCE_SHA}]
        self.tags = [{"name": "refs/tags/v1", "objectId": TAG_SHA}]
        self.definitions = {item["id"]: deepcopy(item) for item in definitions}
        self.drift_on_freeze = False
        self.mismatch_response_revision_on_put = 0
        self.session = _Session(self)

    @staticmethod
    def _p(value):
        return value

    def get_repo(self, project, repo_id):
        return deepcopy(self.repo)

    def list_refs(self, project, repo_id, prefix):
        return deepcopy(self.branches if prefix == "heads/" else self.tags)

    def get_build_definition_full(self, project, pipeline_id):
        return deepcopy(self.definitions[pipeline_id])


class _ReceiptDB:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def get_wave_pipeline_migrations(self, wave, **kwargs):
        return deepcopy(self.rows)


def _service(plan, ado, db=None):
    request = build_cleanup_destructive_request(
        plan,
        "run-1",
        [_repo(plan)],
        disable_pipelines=True,
        add_redirect=True,
        archive_repo=True,
        migration_date="2026-07-21",
        target_web_url="https://github.com",
    )
    service = ADOCleanup(
        ado,
        db or _ReceiptDB(),
        gh=object(),
        approved_plan=plan,
        approved_run_id="run-1",
        destructive_capability_id="capability",
        destructive_request=request,
    )
    finished = []
    service._begin_action = lambda repo, key, kind, before: key
    service._finish_action = lambda receipt, **values: finished.append(
        (receipt, values)
    )
    return service, finished


def test_conversion_receipts_are_plan_run_exact_and_pipeline_filter_scoped():
    receipts = (_receipt(1, "deploy-api"), _receipt(2, "nightly"))
    plan = _plan(pipeline_filter="^deploy", receipts=receipts)
    exact = {
        "project": "Payments", "repo_name": "api", "pipeline_id": 1,
        "pipeline_type": "yaml", "status": "completed",
        "pev_plan_id": plan.plan_id, "pev_run_id": "run-1",
        "gh_org": "octo", "gh_repo": "payments-api",
        "source_fingerprint": "sha256:" + "f" * 64,
    }
    service, _ = _service(plan, _ADO(), _ReceiptDB([exact]))
    service._require_exact_pipeline_conversion_receipts([_repo(plan)])
    exact["pev_run_id"] = "other-run"
    service, _ = _service(plan, _ADO(), _ReceiptDB([exact]))
    with pytest.raises(PermissionError, match="exact completed conversion"):
        service._require_exact_pipeline_conversion_receipts([_repo(plan)])


def test_cleanup_refreshes_signed_credential_versions_before_source_freeze():
    receipt = _receipt(1, "deploy-api")
    plan = _plan(receipts=(receipt,))
    now = datetime.now(timezone.utc)
    version = "2026-07-21T11:59:00Z"
    exact = {
        "project": "Payments", "repo_name": "api", "pipeline_id": 1,
        "pipeline_type": "yaml", "status": "completed",
        "pev_plan_id": plan.plan_id, "pev_run_id": "run-1",
        "gh_org": "octo", "gh_repo": "payments-api",
        "source_fingerprint": "sha256:" + "f" * 64,
        "transform_stats": json.dumps({
            "external_configuration_evidence": {
                "credential_attestation_count": 1,
                "credential_verified_at": (
                    now - timedelta(seconds=5)
                ).isoformat().replace("+00:00", "Z"),
                "credential_valid_until": (
                    now + timedelta(minutes=5)
                ).isoformat().replace("+00:00", "Z"),
                "target_repository_id": "target-node-id",
                "required_secret_names": ["DEPLOY_TOKEN"],
                "secret_metadata_versions": {"DEPLOY_TOKEN": version},
                "repository_checkouts": [],
            }
        }),
    }
    service, _ = _service(plan, _ADO(), _ReceiptDB([exact]))

    class TargetGH:
        secret_version = version

        def get_repo(self, _org, _repo):
            return {"node_id": "target-node-id"}

        def list_actions_secret_metadata(self, _org, _repo):
            return [{"name": "DEPLOY_TOKEN", "updated_at": self.secret_version}]

    service.gh = TargetGH()
    service._require_exact_pipeline_conversion_receipts([_repo(plan)])

    service.gh.secret_version = "2026-07-21T12:00:00Z"
    # Clear the per-call cache by invoking the method again; it is local to the
    # authorization attempt and therefore observes the new live version.
    with pytest.raises(PermissionError, match="credential changed"):
        service._require_exact_pipeline_conversion_receipts([_repo(plan)])


def test_pipeline_disable_uses_approved_revision_and_readback():
    receipts = (_receipt(1, "deploy-api", 3), _receipt(2, "nightly", 7))
    plan = _plan(pipeline_filter="^deploy", receipts=receipts)
    definitions = (
        {
            "id": 1, "name": "deploy-api", "revision": 3,
            "queueStatus": "enabled", "process": {"type": 2},
        },
        {
            "id": 2, "name": "nightly", "revision": 7,
            "queueStatus": "enabled", "process": {"type": 2},
        },
    )
    ado = _ADO(definitions)
    service, finished = _service(plan, ado)
    stats = service._disable_pipelines(_repo(plan), {})
    assert stats["disabled"] == 1
    assert [item[1]["id"] for item in ado.session.puts] == [1]
    assert ado.definitions[1]["revision"] == 4
    assert finished[-1][1]["after"]["source_revision"] == 4


def test_redirect_commit_is_bound_then_frozen_by_archive():
    plan = _plan()
    ado = _ADO()
    service, finished = _service(plan, ado)
    repo = _repo(plan)
    source = service._verified_source(repo)
    redirect = service._add_redirect_readme(repo, source)
    assert redirect["redirect_commit"] == "d" * 40
    archived = service._archive_repo(repo, redirect["source_snapshot"])
    assert archived["status"] == "archived"
    assert ado.repo["isDisabled"] is True
    assert [item[0] for item in finished] == ["add_redirect", "archive_source"]


def test_archive_restores_enabled_state_when_post_freeze_refs_drift():
    plan = _plan()
    ado = _ADO()
    service, finished = _service(plan, ado)
    ado.drift_on_freeze = True
    result = service._archive_repo(
        _repo(plan), service._approved_source_snapshot(_repo(plan))
    )
    assert result["status"] == "failed"
    assert result["restored"] is True
    assert ado.repo["isDisabled"] is False
    assert [payload for _url, payload in ado.session.patches] == [
        {"isDisabled": True}, {"isDisabled": False},
    ]
    assert any(item[0] == "restore_after_archive_drift" for item in finished)


def test_partial_pipeline_disable_is_compensated_and_source_is_restored():
    receipts = (_receipt(1, "deploy-api", 3), _receipt(2, "nightly", 3))
    plan = _plan(receipts=receipts)
    definitions = (
        {
            "id": 1, "name": "deploy-api", "revision": 3,
            "queueStatus": "enabled", "process": {"type": 2},
        },
        {
            "id": 2, "name": "nightly", "revision": 3,
            "queueStatus": "enabled", "process": {"type": 2},
        },
    )
    ado = _ADO(definitions)
    # The second disable returns 2xx and reaches the exact requested state,
    # but its response revision disagrees with readback.
    ado.mismatch_response_revision_on_put = 2
    service, _finished = _service(plan, ado)
    service._verify_live_parity = lambda _repo: None
    service._capture_target_baseline = lambda _repo: {"stable": True}

    result = service._cleanup_one(
        _repo(plan),
        disable_pipelines=True,
        add_redirect=True,
        archive_repo=True,
    )

    assert result["status"] == "failed"
    assert result["actions"]["disable_pipelines"]["compensated"] is True
    assert result["actions"]["archive_rollback"]["status"] == "restored"
    assert result["actions"]["redirect_rollback"]["status"] == "reverted"
    assert ado.repo["isDisabled"] is False
    assert ado.branches[0]["objectId"] == SOURCE_SHA
    assert ado.definitions[1]["queueStatus"] == "enabled"
    assert ado.definitions[2]["queueStatus"] == "enabled"


def test_predispatch_pipeline_drift_unwinds_archive_and_redirect():
    receipts = (_receipt(1, "deploy-api", 3),)
    plan = _plan(receipts=receipts)
    ado = _ADO(({
        "id": 1,
        "name": "deploy-api",
        # The approved inventory was revision 3. This drift is detected before
        # a disable PUT is dispatched and is therefore fully reversible.
        "revision": 4,
        "queueStatus": "enabled",
        "process": {"type": 2},
    },))
    service, _finished = _service(plan, ado)
    service._verify_live_parity = lambda _repo: None
    service._capture_target_baseline = lambda _repo: {"stable": True}

    result = service._cleanup_one(
        _repo(plan),
        disable_pipelines=True,
        add_redirect=True,
        archive_repo=True,
    )

    assert result["status"] == "failed"
    assert result["actions"]["disable_pipelines"] == {
        "status": "failed",
        "compensated": True,
        "ambiguous_remote_state": False,
        "reason": "Pipeline disable failed before an unaccounted mutation",
    }
    assert result["actions"]["archive_rollback"]["status"] == "restored"
    assert result["actions"]["redirect_rollback"]["status"] == "reverted"
    assert ado.session.puts == []
    assert ado.repo["isDisabled"] is False
    assert ado.branches[0]["objectId"] == SOURCE_SHA
