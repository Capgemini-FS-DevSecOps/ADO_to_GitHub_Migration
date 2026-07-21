from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
import yaml

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.rollback import RollbackHandler
from ado2gh.models import (
    MigrationScope,
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    RepoConfig,
)
from ado2gh.pev.contracts import content_digest
from ado2gh.pev.executor import PEVExecutor, plan_wave_id
from ado2gh.pev.planner import MigrationPlanner, _non_secret_config
from ado2gh.reporting.post_migration_validator import FAIL, PASS, PostMigrationValidator
from ado2gh.state.db import StateDB


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(content)}\0".encode("ascii") + content
    ).hexdigest()


class PipelineADO:
    def get_pipeline_yaml_from_git(self, *_args, **_kwargs):
        return "steps:\n  - script: python -m unittest\n"


class PipelineGH:
    def __init__(self):
        self.files: dict[tuple[str, str], str] = {}
        self.pull = {}
        self.puts: list[str] = []
        self.environments: dict[str, dict] = {}
        self.refs = {"main": "a" * 40}
        self.blobs = {}
        self.trees = {"b" * 40: {}}
        self.commits = {
            "a" * 40: {
                "sha": "a" * 40,
                "message": "base",
                "tree": {"sha": "b" * 40},
                "parents": [],
            }
        }

    def get_environment(self, _org, _repo, name):
        return self.environments.get(name)

    def create_environment(self, _org, _repo, name):
        self.environments[name] = {"name": name, "protection_rules": []}
        return {}

    def get_default_branch(self, *_args):
        return "main"

    def get_branch_sha(self, _org, _repo, branch):
        return self.refs[branch]

    def create_branch(self, _org, _repo, branch, sha):
        self.refs[branch] = sha
        tree_sha = self.commits[sha]["tree"]["sha"]
        for path, (_mode, kind, blob_sha) in self.trees[tree_sha].items():
            if kind == "blob":
                self.files[(branch, path)] = blob_sha
                self.puts.append(path)
        return {}

    def get_git_commit(self, _org, _repo, sha):
        return self.commits[sha]

    def get_git_tree(self, _org, _repo, sha, recursive=False):
        return {
            "sha": sha,
            "truncated": False,
            "tree": [
                {"path": path, "mode": mode, "type": kind, "sha": item_sha}
                for path, (mode, kind, item_sha) in sorted(self.trees[sha].items())
            ],
        }

    def create_git_blob(self, _org, _repo, content):
        sha = _blob_sha(content)
        self.blobs[sha] = content
        return {"sha": sha}

    def create_git_tree(self, _org, _repo, *, base_tree_sha, entries):
        leaves = dict(self.trees[base_tree_sha])
        for entry in entries:
            leaves[entry["path"]] = (
                entry["mode"], entry["type"], entry["sha"]
            )
        sha = hashlib.sha1(repr(sorted(leaves.items())).encode()).hexdigest()
        self.trees[sha] = leaves
        return {"sha": sha}

    def create_git_commit(self, _org, _repo, *, message, tree_sha, parents):
        sha = hashlib.sha1(
            (message + tree_sha + "".join(parents)).encode()
        ).hexdigest()
        self.commits[sha] = {
            "sha": sha,
            "message": message,
            "tree": {"sha": tree_sha},
            "parents": [{"sha": parent} for parent in parents],
        }
        return {"sha": sha}

    def get_file_sha(self, _org, _repo, path, ref):
        return self.files.get((ref, path), "")

    def put_file(self, _org, _repo, path, content_b64, branch, _message, sha=""):
        content = base64.b64decode(content_b64)
        self.files[(branch, path)] = _blob_sha(content)
        self.puts.append(path)
        return {"content": {"sha": self.files[(branch, path)]}}

    def find_open_pull_request(self, *_args):
        return self.pull

    def create_pull_request(self, *_args, **_kwargs):
        self.pull = {"number": 17, "html_url": "https://example.invalid/pr/17"}
        return self.pull


def _pipeline_meta() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=7,
        pipeline_name="Build API",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_id="repo-id",
        repo_name="api",
        yaml_path="azure-pipelines.yml",
        stages=[
            PipelineStage(
                name="build",
                jobs=[{
                    "job": "build",
                    "steps": [{"bash": "python -m unittest", "displayName": "Test"}],
                }],
            )
        ],
    )


def test_pipeline_scope_requires_review_pr_then_verifies_exact_default_branch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADO2GH_OUTPUT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ADO2GH_LLM_PROVIDER", "disabled")
    db = StateDB(str(tmp_path / "state.db"))
    db.upsert_pipeline_inventory(_pipeline_meta())
    gh = PipelineGH()
    engine = MigrationEngine(
        {
            "pipeline_conversion": {"require_pinned_action_sha": True},
            "pipeline_delivery": {"mode": "pull_request"},
        },
        PipelineADO(),
        gh,
        db,
    )
    repo = RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=[MigrationScope.PIPELINES.value],
    )

    first = engine._migrate_pipelines(repo, wave_id=44, pipeline_parallel=1)

    assert first["completed"] == 1
    assert first["production_ready"] is False
    assert first["requires_review"] is True
    assert first["delivery"]["state"] == "review_pr"
    assert any(path.startswith(".ado2gh/pipeline-evidence/") for path in gh.puts)
    workflow_path = next(path for path in gh.puts if path.endswith(".yml"))
    workflow_file = next(
        Path(row["workflow_file"])
        for row in db.get_wave_pipeline_migrations(44)
        if row["pipeline_type"] == "yaml"
    )
    parsed = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    checkout = parsed["jobs"]["build"]["steps"][0]["uses"]
    assert checkout.endswith("@11d5960a326750d5838078e36cf38b85af677262")

    # Simulate reviewer merge without changing the reviewed artifact.
    for published_path in gh.puts:
        gh.files[("main", published_path)] = gh.files[
            ("ado2gh/migrated-workflows", published_path)
        ]
    second = engine._migrate_pipelines(repo, wave_id=44, pipeline_parallel=1)

    assert second["skipped"] == 1
    assert second["production_ready"] is True
    assert second["delivery"]["state"] == "remote_verified"
    assert {item["kind"] for item in second["delivery"]["artifact_manifest"]} == {
        "workflow",
        "evidence",
    }

    # A resumed run must not bless a local artifact changed after its
    # completed conversion receipt was committed.
    workflow_file.write_text(
        "name: tampered\non: workflow_dispatch\njobs: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="changed after validation"):
        engine._migrate_pipelines(repo, wave_id=44, pipeline_parallel=1)


def test_pipeline_dry_run_executes_conversion_without_remote_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADO2GH_OUTPUT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ADO2GH_LLM_PROVIDER", "disabled")
    db = StateDB(str(tmp_path / "state.db"))
    db.upsert_pipeline_inventory(_pipeline_meta())
    gh = PipelineGH()
    engine = MigrationEngine(
        {"pipeline_conversion": {"require_pinned_action_sha": True}},
        PipelineADO(),
        gh,
        db,
        dry_run=True,
    )
    repo = RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=[MigrationScope.PIPELINES.value],
    )

    result = engine._migrate_pipelines(repo, wave_id=45, pipeline_parallel=1)

    assert result["production_ready"] is True
    assert result["delivery"]["state"] == "preview_validated"
    assert result["completed"] == 1
    assert gh.puts == []


def test_engine_dry_run_does_not_mask_scope_failures(tmp_path):
    class BrokenADO:
        def get_repo(self, *_args):
            raise RuntimeError("source unavailable")

    engine = MigrationEngine(
        {}, BrokenADO(), object(), StateDB(str(tmp_path / "state.db")),
        dry_run=True,
    )
    repo = RepoConfig(
        ado_project="Payments", ado_repo="api", gh_org="octo", gh_repo="api",
        scopes=[MigrationScope.WORK_ITEMS.value],
    )

    result = engine.migrate_repo(1, repo)

    assert result["status"] == "failed"
    assert result["scopes"][MigrationScope.WORK_ITEMS.value]["status"] == "failed"


def test_git_child_environment_does_not_inherit_unrelated_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-llm-secret")
    monkeypatch.setenv("GH_TOKEN_7", "unrelated-github-secret")
    monkeypatch.setenv("SAFE_BUILD_SETTING", "preserved")

    env = MigrationEngine._git_auth_env(
        str(tmp_path),
        "source",
        "ado2gh",
        "only-required-secret",
        "https://dev.azure.com/example/Payments/_git/api",
        "https://dev.azure.com/example/Payments/_git/api/info/lfs",
    )

    assert "OPENAI_API_KEY" not in env
    assert "GH_TOKEN_7" not in env
    assert env["SAFE_BUILD_SETTING"] == "preserved"
    assert env["ADO2GH_GIT_PASSWORD"] == "only-required-secret"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"].startswith(str(tmp_path))
    config = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert config["http.followRedirects"] == "false"
    assert config["credential.helper"] == ""
    assert config["lfs.url"].endswith("/Payments/_git/api/info/lfs")


class WorkflowOnlyGH:
    def list_workflows(self, *_args):
        return []


def test_zero_pipeline_validation_requires_completed_inventory_receipt(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    validator = PostMigrationValidator(object(), WorkflowOnlyGH(), db)
    repo = RepoConfig(
        ado_project="Payments", ado_repo="api", gh_org="octo", gh_repo="api",
        scopes=[MigrationScope.PIPELINES.value],
    )

    assert validator._check_workflows(repo)["verdict"] == FAIL

    run_id = db.start_pipeline_inventory_run("Payments")
    assert db.finish_pipeline_inventory_run(run_id, status="completed")
    assert validator._check_workflows(repo)["verdict"] == PASS


class DeleteGH:
    def __init__(self, repo_id="R_123"):
        self.deleted = False
        self.repo_id = repo_id

    def repo_exists(self, *_args):
        return True

    def get_repo(self, *_args):
        return {"node_id": self.repo_id, "id": 123}

    def delete_repo(self, *_args):
        self.deleted = True
        return True


def test_rollback_never_deletes_an_adopted_target(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    db.register_repository_mapping(
        "https://dev.azure.com/example",
        "Payments",
        "api",
        "octo",
        "payments-api",
        status="adopted",
    )
    gh = DeleteGH()
    handler = RollbackHandler(gh, db)

    with pytest.raises(
        PermissionError,
        match="Automatic whole-repository deletion is disabled",
    ):
        handler._rollback_repo("octo", "payments-api", False, {})

    assert gh.deleted is False


def test_rollback_never_treats_a_planned_mapping_as_ownership(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    db.register_repository_mapping(
        "https://dev.azure.com/example",
        "Payments",
        "api",
        "octo",
        "payments-api",
        status="planned",
    )
    gh = DeleteGH()

    with pytest.raises(
        PermissionError,
        match="Automatic whole-repository deletion is disabled",
    ):
        RollbackHandler(gh, db)._rollback_repo(
            "octo", "payments-api", False, {"repos_deleted": 0}
        )

    assert gh.deleted is False


def test_rollback_requires_exact_run_and_immutable_target_id(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan_id = "plan-owned"
    run_id = db.create_pev_run(plan_id, run_id="run-owned")
    db.register_repository_mapping(
        "https://dev.azure.com/example",
        "Payments",
        "api",
        "octo",
        "payments-api",
        status="planned",
        fingerprint=plan_id,
    )
    db.record_repository_ownership(
        source_org="https://dev.azure.com/example",
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        plan_id=plan_id,
        run_id=run_id,
        target_repo_id="R_123",
        status="migrated",
    )

    replacement = DeleteGH(repo_id="R_DIFFERENT")
    with pytest.raises(
        PermissionError,
        match="Automatic whole-repository deletion is disabled",
    ):
        RollbackHandler(
            replacement,
            db,
            authorized_plan_id=plan_id,
            authorized_run_id=run_id,
        )._rollback_repo("octo", "payments-api", False, {"repos_deleted": 0})
    assert replacement.deleted is False


class MutableInventoryADO:
    org_url = "https://dev.azure.com/example"

    def __init__(self):
        self.pipeline_present = False

    def get_repo(self, _project, repo):
        return {
            "id": "repo-id",
            "name": repo,
            "defaultBranch": "refs/heads/main",
        }

    def get_repo_commits(self, *_args, **_kwargs):
        return [{"commitId": "a" * 40}]

    def list_refs(self, _project, _repo_id, prefix):
        if prefix == "heads/":
            return [{"name": "refs/heads/main", "objectId": "a" * 40}]
        if prefix == "tags/":
            return []
        raise AssertionError(prefix)

    def list_variable_groups(self, _project):
        return []

    def list_service_connections(self, _project):
        return []

    def list_all_pipelines(self, _project):
        if not self.pipeline_present:
            return iter(())
        return iter(({
            "id": 7,
            "name": "Build API",
            "folder": "\\",
            "configuration": {"type": "yaml"},
        },))

    def list_all_release_pipelines(self, _project):
        return iter(())

    def get_build_definition_full(self, _project, _pipeline_id):
        return {
            "id": 7,
            "name": "Build API",
            "process": {"type": 2, "yamlFilename": "azure-pipelines.yml"},
            "repository": {
                "id": "repo-id",
                "name": "api",
                "type": "TfsGit",
                "defaultBranch": "refs/heads/main",
            },
        }

    def get_pipeline_definition(self, _project, _pipeline_id):
        return {
            "configuration": {
                "type": "yaml",
                "path": "azure-pipelines.yml",
                "repository": {
                    "id": "repo-id",
                    "name": "api",
                    "type": "TfsGit",
                    "defaultBranch": "refs/heads/main",
                },
            },
        }

    def get_pipeline_yaml_from_git(self, *_args, **_kwargs):
        return "steps:\n  - script: python -m unittest\n"

    def get_pipeline_runs(self, *_args, **_kwargs):
        return []


class MappingGH:
    def repo_exists(self, *_args):
        return False


class PreviewEngine:
    def __init__(
        self, _cfg, _ado, _gh, _db, dry_run=False, pipeline_snapshots=None
    ):
        self.dry_run = dry_run
        self.pipeline_snapshots = pipeline_snapshots

    def migrate_repo(self, _wave_id, repo, pipeline_parallel=1):
        del pipeline_parallel
        return {
            "status": "completed",
            "scopes": {
                scope: {"status": "completed", "dry_run": self.dry_run}
                for scope in repo.scopes
            },
        }


def _agent_config(default_scopes):
    return {
        "ado_org_url": "https://dev.azure.com/example",
        "gh_org": "octo",
        "default_scopes": default_scopes,
        "parallel": 1,
        "pipeline_parallel": 1,
        "mapping": {
            "strategy": "project-prefix",
            "existing_target_policy": "fail",
        },
    }


def test_non_secret_plan_view_preserves_security_policy_and_secret_references():
    safe = _non_secret_config({
        "ado_pat": "must-not-leak",
        "gh_token": "must-not-leak-either",
        "allow_inline_secrets": False,
        "target_mapping": {
            "secret_names": ["DEPLOY_TOKEN"],
            "token_secret": "CHECKOUT_TOKEN",
        },
    })

    assert "ado_pat" not in safe
    assert "gh_token" not in safe
    assert safe["allow_inline_secrets"] is False
    assert safe["target_mapping"]["secret_names"] == ["DEPLOY_TOKEN"]
    assert safe["target_mapping"]["token_secret"] == "CHECKOUT_TOKEN"


def _explicit_repo(scopes):
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=scopes,
    )


def test_executor_blocks_pipeline_source_drift_after_plan_approval(tmp_path):
    ado = MutableInventoryADO()
    gh = MappingGH()
    db = StateDB(str(tmp_path / "state.db"))
    cfg = _agent_config([MigrationScope.PIPELINES.value])
    plan = MigrationPlanner(ado, cfg, gh, db).create_plan(
        [_explicit_repo([MigrationScope.PIPELINES.value])]
    )

    ado.pipeline_present = True
    result = PEVExecutor(
        cfg, ado, gh, db, engine_factory=PreviewEngine
    ).execute(plan, approved_plan_id=plan.plan_id)

    assert result.status == "failed"
    repo_result = result.repository_results["Payments/api"]
    assert "Pipeline inventory drift" in repo_result["errors"][0]


def test_verified_pipeline_snapshot_isolated_from_later_db_rescan(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    original = _pipeline_meta()
    original.yaml_content = "steps:\n  - script: python -m unittest\n"
    db.upsert_pipeline_inventory(original)
    approved_receipts = db.get_pipeline_inventory_receipts("Payments", "api")
    approved_digest = content_digest(approved_receipts)

    snapshot = db.get_verified_pipeline_inventory_snapshot(
        "Payments", "api", approved_digest
    )

    # Simulate a concurrent replacement scan after the executor acquired its
    # verified view. The immutable snapshot still contains the approved row,
    # while any new acquisition against the old digest fails closed.
    rescanned = _pipeline_meta()
    rescanned.yaml_content = "steps:\n  - script: invoke-changed-task\n"
    rescanned.migration_notes = ["changed by later inventory generation"]
    db.upsert_pipeline_inventory(rescanned)

    assert snapshot.inventory_digest == approved_digest
    assert snapshot.pipelines()[0].migration_notes == []
    with pytest.raises(RuntimeError, match="Pipeline inventory drift"):
        db.get_verified_pipeline_inventory_snapshot(
            "Payments", "api", approved_digest
        )


class ChangingYamlAfterInventoryADO(MutableInventoryADO):
    def __init__(self):
        super().__init__()
        self.pipeline_present = True
        self.yaml_reads = 0

    def get_pipeline_yaml_from_git(self, *_args, **_kwargs):
        self.yaml_reads += 1
        if self.yaml_reads <= 2:
            return "steps:\n  - script: python -m unittest\n"
        return "steps:\n  - script: invoke-unapproved-task\n"


def test_executor_rejects_yaml_changed_after_verified_inventory(tmp_path):
    ado = ChangingYamlAfterInventoryADO()
    gh = MappingGH()
    db = StateDB(str(tmp_path / "state.db"))
    cfg = _agent_config([MigrationScope.PIPELINES.value])
    plan = MigrationPlanner(ado, cfg, gh, db).create_plan(
        [_explicit_repo([MigrationScope.PIPELINES.value])]
    )

    result = PEVExecutor(cfg, ado, gh, db).execute(
        plan, approved_plan_id=plan.plan_id
    )

    assert result.status == "failed"
    pipeline_detail = result.repository_results["Payments/api"]["scopes"][
        MigrationScope.PIPELINES.value
    ]["detail"]
    assert any("Source YAML drift" in item for item in pipeline_detail["warnings"])
    assert ado.yaml_reads == 3


def test_dry_run_uses_an_isolated_state_database(tmp_path):
    ado = MutableInventoryADO()
    gh = MappingGH()
    db = StateDB(str(tmp_path / "state.db"))
    cfg = _agent_config([MigrationScope.REPO.value])
    plan = MigrationPlanner(ado, cfg, gh).create_plan(
        [_explicit_repo([MigrationScope.REPO.value])]
    )

    result = PEVExecutor(
        cfg, ado, gh, db, engine_factory=PreviewEngine
    ).execute(plan, dry_run=True)

    assert result.status == "dry_run_passed"
    assert db.list_pev_runs() == []
    assert db.get_wave_migrations(plan_wave_id(plan)) == []


def test_dry_run_reports_blockers_instead_of_false_success(tmp_path):
    ado = MutableInventoryADO()
    gh = MappingGH()
    cfg = _agent_config([MigrationScope.PIPELINES.value])
    plan = MigrationPlanner(ado, cfg, gh).create_plan(
        [_explicit_repo([MigrationScope.PIPELINES.value])]
    )
    ado.pipeline_present = True
    durable_db = StateDB(str(tmp_path / "state.db"))

    result = PEVExecutor(
        cfg, ado, gh, durable_db, engine_factory=PreviewEngine
    ).execute(plan, dry_run=True)

    assert result.status == "dry_run_failed"
    assert durable_db.list_pev_runs() == []
