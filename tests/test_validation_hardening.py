from __future__ import annotations

from ado2gh.models import (
    GateStatus,
    PhaseConfig,
    PhaseType,
    RepoConfig,
)
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.pev.source_integrity import create_scope_snapshot
from ado2gh.reporting.post_migration_validator import (
    FAIL,
    PASS,
    WARN,
    PostMigrationValidator,
)
from ado2gh.state.db import StateDB


class FakeADO:
    def __init__(self, branches=None, tags=None, refs_error: Exception = None,
                 default_branch="refs/heads/main"):
        self.branches = branches or {}
        self.tags = tags or {}
        self.refs_error = refs_error
        self.default_branch = default_branch

    def get_repo(self, project, repo):
        return {"id": f"{project}:{repo}", "defaultBranch": self.default_branch}

    def list_refs(self, project, repo_id, prefix):
        if self.refs_error:
            raise self.refs_error
        refs = self.branches if prefix == "heads/" else self.tags
        namespace = "heads" if prefix == "heads/" else "tags"
        return [
            {"name": f"refs/{namespace}/{name}", "objectId": sha}
            for name, sha in refs.items()
        ]


class FakeGH:
    def __init__(self, branches=None, tags=None, workflows=None):
        self.branches = branches or {}
        self.tags = tags or {}
        self.workflows = workflows or []

    def repo_exists(self, org, repo):
        return True

    def get_repo(self, org, repo):
        return {"default_branch": "main"}

    def list_branches(self, org, repo):
        return [
            {"name": name, "commit": {"sha": sha}}
            for name, sha in self.branches.items()
        ]

    def list_tag_refs(self, org, repo):
        return [
            {"ref": f"refs/tags/{name}", "object": {"sha": sha}}
            for name, sha in self.tags.items()
        ]

    def list_workflows(self, org, repo):
        return list(self.workflows)


class FakeInventoryDB:
    def __init__(self, count=0):
        self.count = count

    def inventory_count_for_repo(self, project, repo):
        return self.count


def _repo(scopes=None):
    return RepoConfig("Project A", "service", "octo", "service", scopes or ["repo"])


def test_branch_validation_requires_every_name_and_sha_to_match():
    validator = PostMigrationValidator(
        FakeADO(branches={"main": "a" * 40, "release": "b" * 40}),
        FakeGH(branches={"main": "a" * 40, "release": "c" * 40}),
        FakeInventoryDB(),
    )

    result = validator._check_branch_count(_repo())

    assert result["verdict"] == FAIL
    assert result["ado_count"] == result["gh_count"] == 2
    assert result["mismatched"] == [{
        "name": "release",
        "expected_sha": "b" * 40,
        "actual_sha": "c" * 40,
    }]
    assert result["source_digest"] != result["target_digest"]


def test_ref_api_errors_fail_closed_instead_of_warning():
    validator = PostMigrationValidator(
        FakeADO(refs_error=TimeoutError("ADO unavailable")),
        FakeGH(),
        FakeInventoryDB(),
    )

    result = validator._check_branch_count(_repo())

    assert result["verdict"] == FAIL
    assert result["error_type"] == "TimeoutError"


def test_missing_requested_workflows_is_a_failure():
    validator = PostMigrationValidator(
        FakeADO(), FakeGH(workflows=[{"path": ".github/workflows/one.yml"}]),
        FakeInventoryDB(count=2),
    )

    result = validator._check_workflows(_repo(["repo", "pipelines"]))

    assert result["verdict"] == FAIL
    assert result["expected"] == 2
    assert result["actual"] == 1
    assert result["missing"] == 1


def test_validation_exposes_pev_ready_tag_and_ref_evidence():
    sha = "d" * 40
    validator = PostMigrationValidator(
        FakeADO(branches={"main": sha}, tags={"v1": sha}),
        FakeGH(branches={"main": sha}, tags={"v1": sha}),
        FakeInventoryDB(),
    )

    result = validator.validate([_repo()], max_workers=1)[0]

    assert result["overall"] == PASS
    assert result["evidence_schema"] == "ado2gh.post-migration-validation/v1"
    assert result["checks"]["branches"]["source_digest"]
    assert result["checks"]["tags"]["matched_count"] == 1
    assert result["evidence_summary"]["fail"] == 0


def test_empty_repositories_have_no_default_branch_or_head_to_compare():
    validator = PostMigrationValidator(
        FakeADO(default_branch=None), FakeGH(), FakeInventoryDB()
    )

    result = validator.validate([_repo()], max_workers=1)[0]

    assert result["overall"] == PASS
    assert result["checks"]["default_branch"]["not_applicable"] is True
    assert result["checks"]["head_commit"]["not_applicable"] is True


def test_approved_zero_branch_policy_snapshot_is_not_applicable():
    source_key = "Project A/service"
    validator = PostMigrationValidator(
        FakeADO(),
        FakeGH(),
        FakeInventoryDB(),
        approved_scope_snapshots={
            source_key: {
                "branch_policies": create_scope_snapshot("branch_policies", [])
            }
        },
    )

    result = validator._check_branch_protection(
        _repo(["repo", "branch_policies"])
    )

    assert result["verdict"] == PASS
    assert result["not_applicable"] is True


def test_approved_nonzero_branch_policies_require_semantic_review():
    source_key = "Project A/service"
    snapshot = create_scope_snapshot(
        "branch_policies",
        [{"type": {"id": "build"}, "settings": {"scope": [
            {"refName": "refs/heads/release"}
        ]}}],
    )
    validator = PostMigrationValidator(
        FakeADO(),
        FakeGH(),
        FakeInventoryDB(),
        approved_scope_snapshots={
            source_key: {"branch_policies": snapshot}
        },
    )

    result = validator._check_branch_protection(
        _repo(["repo", "branch_policies"])
    )

    assert result["verdict"] == WARN
    assert result["approved_policy_count"] == 1


def _phase_config(repo_threshold=1.0, pipeline_threshold=1.0):
    return {
        PhaseType.POC: PhaseConfig(
            phase=PhaseType.POC,
            repo_cap=10,
            risk_max=100,
            batch_size=10,
            repo_parallel=1,
            pipeline_parallel=1,
            gate_repo_success_pct=repo_threshold,
            gate_pipeline_success_pct=pipeline_threshold,
            gate_min_completed=1,
        )
    }


def _insert_score(db, project, repo, phase="poc", gh_org="", gh_repo=""):
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO repo_risk_scores "
            "(project,repo_name,total_score,assigned_phase,gh_org,gh_repo) "
            "VALUES (?,?,0,?,?,?)",
            (project, repo, phase, gh_org, gh_repo),
        )


def _insert_repo_status(db, project, repo, status, wave=1):
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO migrations "
            "(wave_id,ado_project,ado_repo,gh_org,gh_repo,scope,status) "
            "VALUES (?,?,?,?,?,'repo',?)",
            (wave, project, repo, "octo", f"{project}-{repo}", status),
        )


def _insert_inventory(db, project, repo, pipeline_id=1):
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO pipeline_inventory "
            "(project,pipeline_id,pipeline_name,repo_name) VALUES (?,?,?,?)",
            (project, pipeline_id, f"pipeline-{pipeline_id}", repo),
        )


def _insert_pipeline_status(db, project, repo, status, pipeline_id=1,
                            workflow_file=None, target_repo=None):
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO pipeline_migrations "
            "(wave_id,project,pipeline_id,pipeline_name,repo_name,gh_org,gh_repo,"
            " status,workflow_file) VALUES (1,?,?,?,?,?,?,?,?)",
            (project, pipeline_id, f"pipeline-{pipeline_id}", repo,
             "octo", target_repo or f"{project}-{repo}", status, workflow_file),
        )


def test_workflow_validation_checks_expected_paths_not_just_counts():
    db = StateDB(":memory:")
    _insert_inventory(db, "Project A", "service")
    _insert_pipeline_status(
        db, "Project A", "service", "completed",
        workflow_file="output/.github/workflows/expected.yml",
        target_repo="service",
    )
    validator = PostMigrationValidator(
        FakeADO(),
        FakeGH(workflows=[{"path": ".github/workflows/unrelated.yml"}]),
        db,
    )

    result = validator._check_workflows(_repo(["repo", "pipelines"]))

    assert result["verdict"] == FAIL
    assert result["missing_paths"] == [".github/workflows/expected.yml"]
    assert result["comparison"] == "pipeline_identity_and_workflow_path"


def test_gate_does_not_mix_same_named_repos_across_projects():
    db = StateDB(":memory:")
    _insert_score(db, "Project A", "shared")
    _insert_repo_status(db, "Project A", "shared", "completed")
    # Same name, different project, and not assigned to this phase.
    _insert_repo_status(db, "Project B", "shared", "failed")

    result = PhaseGateChecker(db, _phase_config()).check(PhaseType.POC)

    assert result.status == GateStatus.PASS
    assert result.repos_completed == result.repos_total == 1


def test_gate_uses_inventory_as_pipeline_denominator_when_attempts_are_missing():
    db = StateDB(":memory:")
    _insert_score(db, "Project A", "service")
    _insert_repo_status(db, "Project A", "service", "completed")
    _insert_inventory(db, "Project A", "service")

    result = PhaseGateChecker(db, _phase_config()).check(PhaseType.POC)

    assert result.status == GateStatus.FAIL
    assert result.pipelines_total == 1
    assert result.pipelines_completed == 0
    assert result.pipeline_success_pct == 0.0


def test_gate_fails_when_an_approved_scope_never_started():
    db = StateDB(":memory:")
    _insert_score(db, "Project A", "service")
    repo = RepoConfig(
        ado_project="Project A",
        ado_repo="service",
        gh_org="octo",
        gh_repo="Project A-service",
        scopes=["repo", "pipelines"],
    )
    db.register_migration_expectations(1, repo, repo.scopes)
    _insert_repo_status(db, "Project A", "service", "completed")
    inventory_run = db.start_pipeline_inventory_run("Project A")
    db.finish_pipeline_inventory_run(inventory_run, status="completed")

    result = PhaseGateChecker(db, _phase_config()).check(PhaseType.POC)

    assert result.status == GateStatus.FAIL
    assert result.repos_completed == 0
    assert any("Missing expected scope receipts" in item for item in result.failures)


def test_gate_does_not_credit_execution_to_the_wrong_github_mapping():
    db = StateDB(":memory:")
    _insert_score(
        db, "Project A", "service", gh_org="octo", gh_repo="approved-target"
    )
    # The source identity matches, but the executor wrote to another target.
    _insert_repo_status(db, "Project A", "service", "completed")

    result = PhaseGateChecker(db, _phase_config()).check(PhaseType.POC)

    assert result.status == GateStatus.FAIL
    assert result.repos_completed == 0


def test_needs_review_is_not_complete_and_blocks_gate():
    db = StateDB(":memory:")
    _insert_score(db, "Project A", "service")
    _insert_repo_status(db, "Project A", "service", "needs_review")
    _insert_inventory(db, "Project A", "service")
    _insert_pipeline_status(db, "Project A", "service", "needs_review")

    result = PhaseGateChecker(
        db, _phase_config(repo_threshold=0.0, pipeline_threshold=0.0)
    ).check(PhaseType.POC)

    assert result.status == GateStatus.FAIL
    assert result.repos_completed == 0
    assert result.pipelines_completed == 0
    assert any("Repos needing review" in failure for failure in result.failures)
    assert any("Pipelines needing review" in failure for failure in result.failures)
