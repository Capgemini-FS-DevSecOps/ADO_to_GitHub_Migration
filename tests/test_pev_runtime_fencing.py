from __future__ import annotations

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.rollback import RollbackHandler
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.contracts import PlanIntegrityError, content_digest
from ado2gh.pev.executor import PEVExecutor
from ado2gh.pev.planner import MigrationPlanner, verify_plan_runtime_context
from ado2gh.pev.validator import PEVValidator
from ado2gh.state.db import StateDB


MAIN_SHA = "a" * 40


class AuthorityADO:
    org_url = "https://dev.azure.com/example"

    def __init__(self) -> None:
        self.organization_id = "ado-org-guid"

    def get_organization_identity(self) -> str:
        return self.organization_id

    def get_repo(self, _project: str, _repo: str) -> dict[str, object]:
        return {
            "id": "ado-repo-guid",
            "name": "api",
            "defaultBranch": "refs/heads/main",
        }

    def list_refs(
        self, _project: str, _repo_id: str, prefix: str
    ) -> list[dict[str, str]]:
        if prefix == "heads/":
            return [{"name": "refs/heads/main", "objectId": MAIN_SHA}]
        if prefix == "tags/":
            return []
        raise AssertionError(prefix)


class AuthorityGH:
    BASE = "https://api.github.com"

    def __init__(self) -> None:
        self.organization_id = "github-org-node-id"

    def get_org(self, org: str) -> dict[str, str]:
        assert org == "octo"
        return {"node_id": self.organization_id}

    def repo_exists(self, org: str, repo: str) -> bool:
        assert (org, repo) == ("octo", "payments-api")
        return False


class NoTargetIO:
    def __init__(self) -> None:
        self.calls = 0

    def repo_exists(self, *_args: object) -> bool:
        self.calls += 1
        raise AssertionError("stale executor reached the GitHub target")


class EngineMustNotStart:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("executor started after an authority/lease rejection")


class OwnedTargetGH:
    def __init__(self) -> None:
        self.deleted_ids: list[str] = []

    def get_repo(self, org: str, repo: str) -> dict[str, object]:
        assert (org, repo) == ("octo", "payments-api")
        return {"node_id": "R_owned", "id": 123}

    def repo_exists(self, org: str, repo: str) -> bool:
        assert (org, repo) == ("octo", "payments-api")
        return True

    def delete_repo_by_id(self, repository_node_id: str) -> bool:
        self.deleted_ids.append(repository_node_id)
        return True


def _config() -> dict[str, object]:
    return {
        "ado_org_url": "https://dev.azure.com/example",
        "gh_org": "octo",
        "default_scopes": [MigrationScope.REPO.value],
        "parallel": 1,
        "mapping": {
            "strategy": "project-prefix",
            "existing_target_policy": "fail",
        },
    }


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=[MigrationScope.REPO.value],
    )


def _plan(ado: AuthorityADO, gh: AuthorityGH):
    return MigrationPlanner(ado, _config(), gh).create_plan([_repo()])


def _persist_completed_execution(
    db: StateDB, plan: object, *, run_id: str = "run-approved"
) -> str:
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


def _persist_owned_target(db: StateDB) -> tuple[str, str]:
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
        target_repo_id="R_owned",
        status="migrated",
    )
    return plan_id, run_id


def test_planner_binds_immutable_runtime_authorities_and_detects_drift():
    ado = AuthorityADO()
    gh = AuthorityGH()
    cfg = _config()
    plan = MigrationPlanner(ado, cfg, gh).create_plan([_repo()])

    assert plan.policy["runtime_context"] == {
        "source_api_url": "https://dev.azure.com/example",
        "source_org_identity": {"kind": "immutable", "id": "ado-org-guid"},
        "target_api_url": "https://api.github.com",
        "target_org_identities": {
            "octo": {"kind": "immutable", "id": "github-org-node-id"}
        },
    }
    verify_plan_runtime_context(plan, cfg, ado, gh)

    ado.organization_id = "replacement-ado-org"
    with pytest.raises(PlanIntegrityError, match="does not match the approved plan"):
        verify_plan_runtime_context(plan, cfg, ado, gh)

    ado.organization_id = "ado-org-guid"
    gh.BASE = "https://github.enterprise.example/api/v3"
    with pytest.raises(PlanIntegrityError, match="does not match the approved plan"):
        verify_plan_runtime_context(plan, cfg, ado, gh)


def test_executor_rejects_runtime_identity_drift_before_starting_engine(tmp_path):
    ado = AuthorityADO()
    gh = AuthorityGH()
    cfg = _config()
    plan = MigrationPlanner(ado, cfg, gh).create_plan([_repo()])
    gh.organization_id = "replacement-github-org"

    with pytest.raises(PlanIntegrityError, match="does not match the approved plan"):
        PEVExecutor(
            cfg,
            ado,
            gh,
            StateDB(str(tmp_path / "state.db")),
            engine_factory=EngineMustNotStart,
        ).execute(plan, approved_plan_id=plan.plan_id)


def test_executor_rejects_competing_target_lease_before_starting_engine(tmp_path):
    ado = AuthorityADO()
    gh = AuthorityGH()
    cfg = _config()
    plan = MigrationPlanner(ado, cfg, gh).create_plan([_repo()])
    db = StateDB(str(tmp_path / "state.db"))
    competing_run = db.create_pev_run(
        "plan-competing", run_id="run-competing"
    )
    competing_tokens = db.acquire_pev_target_leases(
        "plan-competing",
        competing_run,
        "competing-owner",
        [("OCTO", "PAYMENTS-API")],
        ttl_seconds=60,
    )
    assert competing_tokens

    with pytest.raises(RuntimeError, match="targets are already leased"):
        PEVExecutor(
            cfg,
            ado,
            gh,
            db,
            engine_factory=EngineMustNotStart,
        ).execute(plan, approved_plan_id=plan.plan_id)


def test_engine_blocks_stale_fencing_token_before_target_io_or_state_write(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_a = db.create_pev_run("plan-a", run_id="run-a")
    stale_tokens = db.acquire_pev_target_leases(
        "plan-a",
        run_a,
        "owner-a",
        [("octo", "payments-api")],
        ttl_seconds=60,
    )
    assert stale_tokens
    assert db.release_pev_target_leases(
        "plan-a", run_a, "owner-a", stale_tokens
    ) == 1

    run_b = db.create_pev_run("plan-b", run_id="run-b")
    current_tokens = db.acquire_pev_target_leases(
        "plan-b",
        run_b,
        "owner-b",
        [("octo", "payments-api")],
        ttl_seconds=60,
    )
    assert current_tokens
    assert next(iter(current_tokens.values())) > next(iter(stale_tokens.values()))

    gh = NoTargetIO()
    engine = MigrationEngine(
        {
            "pev_plan_id": "plan-a",
            "pev_run_id": run_a,
            "pev_target_fencing_required": True,
            "pev_target_lease_owner": "owner-a",
            "pev_target_fencing_tokens": stale_tokens,
        },
        object(),
        gh,
        db,
    )

    with pytest.raises(RuntimeError, match="target lease was lost"):
        engine.migrate_repo(17, _repo())

    assert gh.calls == 0
    assert db.get_wave_migrations(17) == []


def test_validator_rejects_competing_target_lease_before_remote_checks(tmp_path):
    ado = AuthorityADO()
    gh = AuthorityGH()
    plan = _plan(ado, gh)
    db = StateDB(str(tmp_path / "state.db"))
    run_id = _persist_completed_execution(db, plan)

    competing_run = db.create_pev_run(
        "plan-competing", run_id="run-competing"
    )
    competing_tokens = db.acquire_pev_target_leases(
        "plan-competing",
        competing_run,
        "competing-validator",
        [("octo", "payments-api")],
        ttl_seconds=60,
    )
    assert competing_tokens

    with pytest.raises(RuntimeError, match="being modified by another plan"):
        PEVValidator(object(), object(), db).validate(plan, run_id)


def test_rollback_delete_is_blocked_by_active_target_lease(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    plan_id, run_id = _persist_owned_target(db)
    active_tokens = db.acquire_pev_target_leases(
        plan_id,
        run_id,
        "active-executor",
        [("octo", "payments-api")],
        ttl_seconds=300,
    )
    assert active_tokens
    gh = OwnedTargetGH()

    with pytest.raises(RuntimeError, match="leased by an executor or validator"):
        RollbackHandler(
            gh,
            db,
            authorized_plan_id=plan_id,
            authorized_run_id=run_id,
        )._rollback_repo(
            "octo", "payments-api", False, {"repos_deleted": 0}
        )

    assert gh.deleted_ids == []
    db.assert_pev_target_lease(
        "octo",
        "payments-api",
        plan_id=plan_id,
        run_id=run_id,
        lease_owner="active-executor",
        fencing_token=next(iter(active_tokens.values())),
    )


def test_quarantine_survives_executor_release_and_blocks_competing_plan(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_a = db.create_pev_run("plan-a", run_id="run-a")
    run_b = db.create_pev_run("plan-b", run_id="run-b")
    tokens = db.acquire_pev_target_leases(
        "plan-a",
        run_a,
        "executor-owner",
        [("octo", "payments-api")],
        ttl_seconds=300,
    )
    assert tokens
    key, token = next(iter(tokens.items()))

    assert db.quarantine_pev_target_lease(
        "octo",
        "payments-api",
        plan_id="plan-a",
        run_id=run_a,
        lease_owner="executor-owner",
        fencing_token=token,
        ttl_seconds=3_600,
    )
    with db._conn() as conn:
        quarantined = conn.execute(
            "SELECT lease_owner,lease_expires_at FROM pev_target_leases "
            "WHERE target_key=?",
            (key,),
        ).fetchone()
    assert quarantined["lease_owner"] == "quarantine:executor-owner"

    assert db.release_pev_target_leases(
        "plan-a", run_a, "executor-owner", tokens
    ) == 0
    assert db.acquire_pev_target_leases(
        "plan-b",
        run_b,
        "competing-owner",
        [("OCTO", "PAYMENTS-API")],
        ttl_seconds=300,
    ) is None


def test_short_heartbeat_renewal_never_shortens_long_target_lease(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_id = db.create_pev_run("plan-long", run_id="run-long")
    tokens = db.acquire_pev_target_leases(
        "plan-long",
        run_id,
        "executor-owner",
        [("octo", "payments-api")],
        ttl_seconds=7_200,
    )
    assert tokens
    key = next(iter(tokens))
    with db._conn() as conn:
        before = conn.execute(
            "SELECT lease_expires_at FROM pev_target_leases WHERE target_key=?",
            (key,),
        ).fetchone()["lease_expires_at"]

    assert db.renew_pev_target_leases(
        "plan-long",
        run_id,
        "executor-owner",
        tokens,
        ttl_seconds=300,
    )
    with db._conn() as conn:
        after = conn.execute(
            "SELECT lease_expires_at FROM pev_target_leases WHERE target_key=?",
            (key,),
        ).fetchone()["lease_expires_at"]

    assert after == before
