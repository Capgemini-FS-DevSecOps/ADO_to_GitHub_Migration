from __future__ import annotations

import pytest

from ado2gh.models import MigrationScope
from ado2gh.pev.contracts import (
    MigrationPlan,
    PlannedRepository,
    PlanTask,
    compute_source_refs_digest,
    content_digest,
)
from ado2gh.pev.executor import PEVExecutor
from ado2gh.pev.validator import PEVValidator
from ado2gh.state.db import StateDB


class PassingPostMigrationValidator:
    calls = 0

    def __init__(self, *_args):
        pass

    def validate(self, repos, output_path=None, max_workers=6):
        del output_path, max_workers
        type(self).calls += 1
        return [
            {
                "ado_project": repo.ado_project,
                "ado_repo": repo.ado_repo,
                "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
                "overall": "PASS",
                "checks": {},
            }
            for repo in repos
        ]


@pytest.fixture(autouse=True)
def _passing_remote_validator(monkeypatch):
    PassingPostMigrationValidator.calls = 0
    monkeypatch.setattr(
        "ado2gh.pev.validator.PostMigrationValidator",
        PassingPostMigrationValidator,
    )


def _plan() -> MigrationPlan:
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
            "pipelines": [],
            "pipeline_count": 0,
            "inventory_digest": content_digest([]),
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
    branches = (("refs/heads/main", "a" * 40),)
    tags = (("refs/tags/v1", "b" * 40),)
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
                source_repo_id="repo-id",
                default_branch="main",
                source_head_sha="a" * 40,
                source_branch_refs=branches,
                source_tag_refs=tags,
                source_refs_digest=compute_source_refs_digest(branches, tags),
            ),
        ),
        tasks=(
            preflight,
            inventory,
            repo_execution,
            pipeline_execution,
            validation,
        ),
        policy={"mapping": {"existing_target_policy": "fail"}},
        config_digest=content_digest({"approved": "configuration"}),
    )


def _persist_run(
    db: StateDB,
    plan: MigrationPlan,
    *,
    run_status: str = "executed",
    task_statuses: dict[tuple[str, str], str] | None = None,
    skip_task: str = "",
    digest_override: dict[str, str] | None = None,
) -> str:
    run_id = "run-state-integrity"
    db.upsert_pev_run(
        run_id,
        plan.plan_id,
        status=run_status,
        config_digest=plan.config_digest,
    )
    task_statuses = task_statuses or {}
    digest_override = digest_override or {}
    for task in plan.tasks:
        if task.task_id == skip_task:
            continue
        default_status = "pending" if task.kind == "validate" else "completed"
        db.upsert_pev_task(
            run_id=run_id,
            kind=task.kind,
            input_digest=digest_override.get(
                task.task_id, content_digest(task.to_dict())
            ),
            source_ref=task.source_key,
            target_ref=task.target_key,
            status=task_statuses.get((task.kind, task.scope), default_status),
            task_id=PEVExecutor._db_task_id(run_id, task),
            strategy=task.execution_mode,
            dependencies=list(task.dependencies),
            idempotency_key=f"{plan.plan_id}:{task.task_id}",
            result={"plan_task_id": task.task_id, "scope": task.scope},
        )
    return run_id


def test_validator_accepts_exact_completed_execution_receipt(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan()
    run_id = _persist_run(db, plan)

    report = PEVValidator(object(), object(), db).validate(plan, run_id)

    assert report.status == "passed"
    assert db.get_pev_run(run_id)["status"] == "completed"
    assert PassingPostMigrationValidator.calls == 1


def test_validator_can_repeat_a_completed_validation(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan()
    run_id = _persist_run(db, plan)

    first = PEVValidator(object(), object(), db).validate(plan, run_id)
    second = PEVValidator(object(), object(), db).validate(plan, run_id)

    assert first.status == second.status == "passed"
    assert db.get_pev_run(run_id)["status"] == "completed"
    assert PassingPostMigrationValidator.calls == 2


@pytest.mark.parametrize("run_status", ["planned", "pending", "dry_run"])
def test_validator_rejects_non_execution_runs_without_remote_checks(
    tmp_path, run_status
):
    db = StateDB(str(tmp_path / f"{run_status}.db"))
    plan = _plan()
    run_id = _persist_run(db, plan, run_status=run_status)

    with pytest.raises(ValueError, match="not ready for validation"):
        PEVValidator(object(), object(), db).validate(plan, run_id)

    assert db.get_pev_run(run_id)["status"] == run_status
    assert PassingPostMigrationValidator.calls == 0


def test_validator_rejects_missing_or_injected_plan_tasks(tmp_path):
    plan = _plan()

    missing_db = StateDB(str(tmp_path / "missing.db"))
    missing_run = _persist_run(missing_db, plan, skip_task="execute-pipelines")
    with pytest.raises(ValueError, match="task graph.*missing="):
        PEVValidator(object(), object(), missing_db).validate(plan, missing_run)

    extra_db = StateDB(str(tmp_path / "extra.db"))
    extra_run = _persist_run(extra_db, plan)
    extra_db.upsert_pev_task(
        run_id=extra_run,
        kind="execute",
        input_digest=content_digest({"injected": True}),
        source_ref="Payments/api",
        target_ref="octo/payments-api",
        status="completed",
        task_id="task_injected",
        idempotency_key="injected",
    )
    with pytest.raises(ValueError, match="task graph.*extra="):
        PEVValidator(object(), object(), extra_db).validate(plan, extra_run)

    assert PassingPostMigrationValidator.calls == 0


def test_validator_rejects_task_digest_mismatch(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan()
    run_id = _persist_run(
        db,
        plan,
        digest_override={"execute-repo": "0" * 64},
    )

    with pytest.raises(ValueError, match="input_digest"):
        PEVValidator(object(), object(), db).validate(plan, run_id)

    assert PassingPostMigrationValidator.calls == 0


@pytest.mark.parametrize(
    ("kind", "scope", "task_status"),
    [
        ("preflight", "", "pending"),
        ("inventory", MigrationScope.PIPELINES.value, "failed"),
        ("execute", MigrationScope.REPO.value, "blocked"),
    ],
)
def test_non_completed_execution_task_forces_validation_failure(
    tmp_path, kind, scope, task_status
):
    db = StateDB(str(tmp_path / f"{kind}-{task_status}.db"))
    plan = _plan()
    run_id = _persist_run(
        db,
        plan,
        task_statuses={(kind, scope): task_status},
    )

    report = PEVValidator(object(), object(), db).validate(plan, run_id)

    assert report.status == "failed"
    assert db.get_pev_run(run_id)["status"] == "validation_failed"
    assert any(f"{kind}" in item["detail"] for item in report.failures)


def test_execution_task_needing_review_cannot_be_promoted_to_completed(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan = _plan()
    run_id = _persist_run(
        db,
        plan,
        task_statuses={
            ("execute", MigrationScope.PIPELINES.value): "needs_review"
        },
    )

    report = PEVValidator(object(), object(), db).validate(plan, run_id)

    assert report.status == "needs_review"
    assert db.get_pev_run(run_id)["status"] == "needs_review"
    assert any(item["category"] == "execution_review" for item in report.warnings)


@pytest.mark.parametrize(
    ("run_status", "expected_report"),
    [("failed", "failed"), ("needs_review", "needs_review")],
)
def test_run_terminal_state_sets_a_validation_floor(
    tmp_path, run_status, expected_report
):
    db = StateDB(str(tmp_path / f"{run_status}.db"))
    plan = _plan()
    run_id = _persist_run(db, plan, run_status=run_status)

    report = PEVValidator(object(), object(), db).validate(plan, run_id)

    assert report.status == expected_report
    assert db.get_pev_run(run_id)["status"] == (
        "validation_failed" if expected_report == "failed" else "needs_review"
    )
