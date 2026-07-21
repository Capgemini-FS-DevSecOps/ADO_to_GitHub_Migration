from __future__ import annotations

import json

from ado2gh.models import MigrationScope, PipelineMetadata, PipelineType
from ado2gh.pev.contracts import (
    MigrationPlan,
    PlannedRepository,
    PlanTask,
    compute_source_refs_digest,
    content_digest,
)
from ado2gh.pev.executor import PEVExecutor, plan_wave_id
from ado2gh.pev.validator import PEVValidator
from ado2gh.state.db import StateDB


SOURCE_SHA = "a" * 40
TAG_SHA = "b" * 40
WORKFLOW_SHA = "c" * 40


class ExactWorkflowGH:
    def __init__(
        self,
        workflow_paths: list[str],
        *,
        target_sha: str = SOURCE_SHA,
        extra_tree_path: str = "",
        extra_branches: dict[str, str] | None = None,
    ):
        self.workflow_paths = workflow_paths
        self.target_sha = target_sha
        self.extra_tree_path = extra_tree_path
        self.extra_branches = extra_branches or {}

    def repo_exists(self, *_args):
        return True

    def get_repo(self, *_args):
        return {"default_branch": "main"}

    def list_branches(self, *_args):
        return [
            {"name": "main", "commit": {"sha": self.target_sha}},
            *[
                {"name": name, "commit": {"sha": sha}}
                for name, sha in self.extra_branches.items()
            ],
        ]

    def list_tag_refs(self, *_args):
        return [{"ref": "refs/tags/v1", "object": {"sha": TAG_SHA}}]

    def list_workflows(self, *_args):
        return [{"path": path} for path in self.workflow_paths]

    def get_default_branch(self, *_args):
        return "main"

    def get_file_sha(self, *_args):
        return WORKFLOW_SHA

    def _get(self, path, params=None):
        del params
        if "/compare/" in path:
            return {
                "status": "ahead",
                "merge_base_commit": {"sha": SOURCE_SHA},
            }
        if f"/git/commits/{SOURCE_SHA}" in path:
            return {"tree": {"sha": "1" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "2" * 40}}
        if f"/git/trees/{'1' * 40}" in path:
            return {
                "truncated": False,
                "tree": [{
                    "path": "README.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "d" * 40,
                }],
            }
        if f"/git/trees/{'2' * 40}" in path:
            tree = [
                {
                    "path": "README.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "d" * 40,
                },
                {
                    "path": ".github/workflows/build-api.yml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": WORKFLOW_SHA,
                },
                {
                    "path": ".ado2gh/pipeline-evidence/7-evidence.json",
                    "mode": "100644",
                    "type": "blob",
                    "sha": WORKFLOW_SHA,
                },
            ]
            if self.extra_tree_path:
                tree.append({
                    "path": self.extra_tree_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": "f" * 40,
                })
            return {"truncated": False, "tree": tree}
        raise AssertionError(f"Unexpected GitHub API path: {path}")


def _receipt(pipeline_id: int, name: str) -> dict:
    return {
        "pipeline_id": pipeline_id,
        "pipeline_name": name,
        "pipeline_type": "yaml",
        "repo_id": "source-repo-id",
        "repo_name": "api",
        "source_yaml_path": f"pipelines/{pipeline_id}.yml",
        "source_yaml_sha256": f"{pipeline_id:064x}"[-64:],
        "metadata_schema_version": 2,
        "inventory_ruleset_version": "test-rules/v1",
        "semantic_metadata_sha256": f"{pipeline_id + 100:064x}"[-64:],
    }


def _plan(receipts: list[dict], pipeline_filter: str = "") -> MigrationPlan:
    source = "Payments/api"
    target = "octo/payments-api"
    preflight = PlanTask(
        "preflight",
        "preflight",
        source,
        target,
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
        "inventory",
        "inventory",
        source,
        target,
        scope=MigrationScope.PIPELINES.value,
        dependencies=(preflight.task_id,),
        metadata={
            "pipelines": receipts,
            "pipeline_count": len(receipts),
            "inventory_digest": content_digest(receipts),
        },
    )
    repo_execution = PlanTask(
        "execute-repo",
        "execute",
        source,
        target,
        scope=MigrationScope.REPO.value,
        dependencies=(preflight.task_id,),
    )
    pipeline_execution = PlanTask(
        "execute-pipelines",
        "execute",
        source,
        target,
        scope=MigrationScope.PIPELINES.value,
        dependencies=(
            preflight.task_id,
            inventory.task_id,
            repo_execution.task_id,
        ),
    )
    validation = PlanTask(
        "validate",
        "validate",
        source,
        target,
        dependencies=(repo_execution.task_id, pipeline_execution.task_id),
    )
    branches = (("refs/heads/main", SOURCE_SHA),)
    tags = (("refs/tags/v1", TAG_SHA),)
    return MigrationPlan.create(
        source_org_url="https://dev.azure.com/example",
        target_org="octo",
        repositories=(
            PlannedRepository(
                ado_project="Payments",
                ado_repo="api",
                gh_org="octo",
                gh_repo="payments-api",
                scopes=(MigrationScope.REPO.value, MigrationScope.PIPELINES.value),
                source_repo_id="source-repo-id",
                default_branch="main",
                source_head_sha=SOURCE_SHA,
                source_branch_refs=branches,
                source_tag_refs=tags,
                source_refs_digest=compute_source_refs_digest(branches, tags),
                pipeline_filter=pipeline_filter,
            ),
        ),
        tasks=(
            preflight,
            inventory,
            repo_execution,
            pipeline_execution,
            validation,
        ),
        policy={
            "mapping": {"existing_target_policy": "fail"},
            "pipeline_delivery": {
                "mode": "pull_request",
                "branch": "ado2gh/migrated-workflows",
            },
        },
        config_digest=content_digest({"approved": "configuration"}),
    )


def _persist_completed_run(db: StateDB, plan: MigrationPlan) -> str:
    run_id = f"run-{plan.plan_id[-12:]}"
    db.upsert_pev_run(
        run_id,
        plan.plan_id,
        status="executed",
        config_digest=plan.config_digest,
    )
    for task in plan.tasks:
        db.upsert_pev_task(
            run_id=run_id,
            kind=task.kind,
            input_digest=content_digest(task.to_dict()),
            source_ref=task.source_key,
            target_ref=task.target_key,
            status="pending" if task.kind == "validate" else "completed",
            task_id=PEVExecutor._db_task_id(run_id, task),
            strategy=task.execution_mode,
            dependencies=list(task.dependencies),
            idempotency_key=f"{plan.plan_id}:{task.task_id}",
            result={"plan_task_id": task.task_id, "scope": task.scope},
        )
    return run_id


def _insert_conversion(
    db: StateDB,
    plan: MigrationPlan,
    pipeline_id: int,
    name: str,
    workflow_name: str,
) -> None:
    inventory_task = next(task for task in plan.tasks if task.kind == "inventory")
    receipt = next(
        item for item in inventory_task.metadata["pipelines"]
        if item["pipeline_id"] == pipeline_id
    )
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO pipeline_migrations "
            "(wave_id,project,pipeline_id,pipeline_type,pipeline_name,repo_name,"
            "gh_org,gh_repo,status,workflow_file,transform_stats,pev_plan_id,"
            "pev_run_id,source_fingerprint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                plan_wave_id(plan),
                "Payments",
                pipeline_id,
                "yaml",
                name,
                "api",
                "octo",
                "payments-api",
                "completed",
                f"artifacts/.github/workflows/{workflow_name}",
                json.dumps({
                    "production_ready": True,
                    "workflow_blob_sha": WORKFLOW_SHA,
                    "evidence_file": f"artifacts/{pipeline_id}-evidence.json",
                    "evidence_blob_sha": WORKFLOW_SHA,
                    "evidence_remote_path": (
                        f".ado2gh/pipeline-evidence/{pipeline_id}-evidence.json"
                    ),
                    "approved_inventory_digest": inventory_task.metadata[
                        "inventory_digest"
                    ],
                    "approved_pipeline_receipt_digest": content_digest(receipt),
                    "conversion_source_fingerprint": "sha256:" + "9" * 64,
                }),
                plan.plan_id,
                f"run-{plan.plan_id[-12:]}",
                "sha256:" + "9" * 64,
            ),
        )


def _seed_mutable_inventory(db: StateDB, pipeline_id: int, name: str) -> None:
    db.upsert_pipeline_inventory(
        PipelineMetadata(
            pipeline_id=pipeline_id,
            pipeline_name=name,
            pipeline_type=PipelineType.YAML,
            project="Payments",
            repo_id="source-repo-id",
            repo_name="api",
            yaml_path=f"pipelines/{pipeline_id}.yml",
        )
    )
    inventory_run = db.start_pipeline_inventory_run("Payments")
    assert db.finish_pipeline_inventory_run(inventory_run, status="completed")


def _validate(
    db: StateDB,
    plan: MigrationPlan,
    paths: list[str],
    gh: ExactWorkflowGH | None = None,
):
    run_id = _persist_completed_run(db, plan)
    return PEVValidator(
        object(), gh or ExactWorkflowGH(paths), db
    ).validate(plan, run_id)


def test_pev_validation_ignores_mutable_inventory_drift(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    approved = _receipt(7, "Build API")
    plan = _plan([approved])
    _seed_mutable_inventory(db, 99, "Later Rescan")
    _insert_conversion(db, plan, 7, "Build API", "build-api.yml")

    report = _validate(db, plan, [".github/workflows/build-api.yml"])

    assert report.status == "passed"
    workflow = report.repository_results[0]["checks"]["workflows"]
    assert workflow["comparison"] == "approved_pipeline_receipts"
    assert workflow["ado_pipelines"] == 1
    assert workflow["approved_wave_id"] == plan_wave_id(plan)


def test_other_wave_conversion_cannot_satisfy_approved_plan(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    approved = _receipt(7, "Build API")
    plan = _plan([approved])
    _seed_mutable_inventory(db, 7, "Build API")
    _insert_conversion(db, plan, 7, "Build API", "build-api.yml")
    with db._conn() as conn:
        conn.execute(
            "UPDATE pipeline_migrations SET wave_id=? WHERE pev_plan_id=?",
            (plan_wave_id(plan) + 1, plan.plan_id),
        )

    report = _validate(db, plan, [".github/workflows/build-api.yml"])

    assert report.status == "failed"
    workflow = report.repository_results[0]["checks"]["workflows"]
    assert workflow["missing_pipeline_identities"] == [[7, "yaml"]]


def test_same_wave_receipt_from_another_run_cannot_satisfy_plan(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan([_receipt(7, "Build API")])
    _insert_conversion(db, plan, 7, "Build API", "build-api.yml")
    with db._conn() as conn:
        conn.execute(
            "UPDATE pipeline_migrations SET pev_run_id='run-other' "
            "WHERE pev_plan_id=?",
            (plan.plan_id,),
        )

    report = _validate(db, plan, [".github/workflows/build-api.yml"])

    assert report.status == "failed"
    workflow = report.repository_results[0]["checks"]["workflows"]
    assert workflow["verdict"] == "FAIL"
    assert "exact approved PEV plan and run" in workflow["detail"]


def test_approved_zero_pipeline_snapshot_is_authoritative(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan([])
    _seed_mutable_inventory(db, 55, "Unapproved Later Pipeline")
    # A receipt for a different wave cannot change an authoritative empty plan.
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO pipeline_migrations "
            "(wave_id,project,pipeline_id,pipeline_type,pipeline_name,repo_name,"
            "gh_org,gh_repo,status,workflow_file,pev_plan_id,pev_run_id,"
            "source_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                plan_wave_id(plan) + 1,
                "Payments",
                55,
                "yaml",
                "Unapproved Later Pipeline",
                "api",
                "octo",
                "payments-api",
                "completed",
                "later.yml",
                "other-plan",
                "other-run",
                "sha256:" + "8" * 64,
            ),
        )

    report = _validate(db, plan, [".github/workflows/unrelated.yml"])

    assert report.status == "passed"
    workflow = report.repository_results[0]["checks"]["workflows"]
    assert workflow["ado_pipelines"] == 0
    assert workflow["expected_paths"] == []


def test_pipeline_filter_applies_to_approved_receipts(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan(
        [_receipt(7, "Build API"), _receipt(8, "Deploy API")],
        pipeline_filter=r"^Build\b",
    )
    _insert_conversion(db, plan, 7, "Build API", "build-api.yml")

    report = _validate(db, plan, [".github/workflows/build-api.yml"])

    assert report.status == "passed"
    workflow = report.repository_results[0]["checks"]["workflows"]
    assert workflow["ado_pipelines"] == 1
    assert workflow["missing_pipeline_identities"] == []


def test_default_branch_allows_only_complete_exact_pipeline_overlay(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan([_receipt(7, "Build API")])
    _insert_conversion(db, plan, 7, "Build API", "build-api.yml")
    target_sha = "e" * 40
    gh = ExactWorkflowGH(
        [".github/workflows/build-api.yml"],
        target_sha=target_sha,
        extra_branches={"ado2gh/migrated-workflows": target_sha},
    )

    report = _validate(db, plan, [], gh)

    assert report.status == "passed"
    checks = report.repository_results[0]["checks"]
    assert checks["head_commit"]["overlay"]["verdict"] == "PASS"
    assert checks["branches"]["allowed_overlay_branches"] == [
        "ado2gh/migrated-workflows"
    ]
    assert set(checks["head_commit"]["overlay"]["changed_paths"]) == {
        ".github/workflows/build-api.yml",
        ".ado2gh/pipeline-evidence/7-evidence.json",
    }


def test_default_branch_overlay_fails_on_any_unapproved_tree_change(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan([_receipt(7, "Build API")])
    _insert_conversion(db, plan, 7, "Build API", "build-api.yml")
    gh = ExactWorkflowGH(
        [".github/workflows/build-api.yml"],
        target_sha="e" * 40,
        extra_tree_path="src/backdoor.py",
    )

    report = _validate(db, plan, [], gh)

    assert report.status == "failed"
    overlay = report.repository_results[0]["checks"]["head_commit"]["overlay"]
    assert overlay["verdict"] == "FAIL"
    assert overlay["unexpected_paths"] == ["src/backdoor.py"]
