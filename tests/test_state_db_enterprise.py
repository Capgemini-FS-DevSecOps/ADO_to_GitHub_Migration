from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from ado2gh.models import (
    MigrationStatus,
    PipelineMetadata,
    PipelineType,
    PipelineVariable,
    PipelineStage,
)
from ado2gh.state.db import StateDB


def _pipeline(kind: PipelineType, name: str = "pipeline") -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=7,
        pipeline_name=name,
        pipeline_type=kind,
        project="Project",
        repo_name="Repo",
        yaml_content="secret: do-not-persist",
    )


def test_schema_version_and_transaction_rollback(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    assert db.schema_version == StateDB.CURRENT_SCHEMA_VERSION
    with db._conn() as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    assert int(version) == StateDB.CURRENT_SCHEMA_VERSION


def test_target_leases_fence_competing_plans_and_renew_task_claims(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_a = db.create_pev_run("plan-a", run_id="run-a")
    run_b = db.create_pev_run("plan-b", run_id="run-b")
    tokens = db.acquire_pev_target_leases(
        "plan-a", run_a, "worker-a", [("Octo", "Service")],
        ttl_seconds=60,
    )
    assert tokens and len(tokens) == 1
    assert db.acquire_pev_target_leases(
        "plan-b", run_b, "worker-b", [("octo", "service")],
        ttl_seconds=60,
    ) is None

    key, token = next(iter(tokens.items()))
    db.assert_pev_target_lease(
        "octo", "service", plan_id="plan-a", run_id=run_a,
        lease_owner="worker-a", fencing_token=token,
    )
    assert db.renew_pev_target_leases(
        "plan-a", run_a, "worker-a", tokens, ttl_seconds=60
    )
    assert db.release_pev_target_leases(
        "plan-a", run_a, "worker-a", tokens
    ) == 1
    replacement = db.acquire_pev_target_leases(
        "plan-b", run_b, "worker-b", [("octo", "service")],
        ttl_seconds=60,
    )
    assert replacement and next(iter(replacement.values())) > token
    with pytest.raises(RuntimeError, match="lease was lost"):
        db.assert_pev_target_lease(
            "octo", "service", plan_id="plan-a", run_id=run_a,
            lease_owner="worker-a", fencing_token=token,
        )


def test_indefinite_target_quarantine_requires_audited_release(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_a = db.create_pev_run("plan-a", run_id="run-a")
    run_b = db.create_pev_run("plan-b", run_id="run-b")
    tokens = db.acquire_pev_target_leases(
        "plan-a", run_a, "worker-a", [("octo", "service")], ttl_seconds=60
    )
    token = next(iter(tokens.values()))
    assert db.quarantine_pev_target_lease(
        "octo",
        "service",
        plan_id="plan-a",
        run_id=run_a,
        lease_owner="worker-a",
        fencing_token=token,
        ttl_seconds=0,
    )
    assert db.release_pev_target_leases(
        "plan-a", run_a, "worker-a", tokens
    ) == 0
    assert db.acquire_pev_target_leases(
        "plan-b", run_b, "worker-b", [("octo", "service")], ttl_seconds=60
    ) is None

    evidence = db.release_pev_target_quarantine(
        "octo",
        "service",
        plan_id="plan-a",
        run_id=run_a,
        approval_ticket="CHG-42",
        observed_target_repo_id="R_1",
    )
    assert evidence["fencing_token"] == token
    assert db.acquire_pev_target_leases(
        "plan-b", run_b, "worker-b", [("octo", "service")], ttl_seconds=60
    )
    audit = db.list_validation_evidence(run_a)
    assert any(row["category"] == "target_quarantine_release" for row in audit)

    with pytest.raises(RuntimeError):
        with db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO pev_runs "
                "(run_id,plan_id,status,config_digest,summary_json,created_at,updated_at) "
                "VALUES ('rolled-back','p','planned','','{}','now','now')"
            )
            raise RuntimeError("rollback")
    assert db.get_pev_run("rolled-back") is None


def test_unversioned_legacy_database_is_upgraded_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(StateDB.SCHEMA)
    conn.execute(
        "INSERT INTO pipeline_inventory "
        "(project,pipeline_id,pipeline_name,pipeline_type) VALUES ('P',1,'build','yaml')"
    )
    conn.execute(
        "INSERT INTO wave_runs(wave_id,started_at,status) VALUES (1,'a','in_progress')"
    )
    conn.execute(
        "INSERT INTO wave_runs(wave_id,started_at,status) VALUES (1,'b','in_progress')"
    )
    conn.execute("PRAGMA user_version=0")
    conn.commit()
    conn.close()

    db = StateDB(str(path))

    assert db.schema_version == StateDB.CURRENT_SCHEMA_VERSION
    assert db.inventory_count() == 1
    with db._conn() as upgraded:
        active = upgraded.execute(
            "SELECT COUNT(*) FROM wave_runs WHERE completed_at IS NULL"
        ).fetchone()[0]
        interrupted = upgraded.execute(
            "SELECT COUNT(*) FROM wave_runs WHERE status='interrupted'"
        ).fetchone()[0]
    assert active == 1
    assert interrupted == 1


def test_repository_mapping_is_idempotent_and_collision_safe(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))

    def register():
        return db.register_repository_mapping(
            "https://dev.azure.com/example", "Project", "Repo",
            "target-org", "target-repo",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: register(), range(16)))
    assert len(set(ids)) == 1
    assert len(db.list_repository_mappings()) == 1

    with pytest.raises(ValueError, match="already mapped"):
        db.register_repository_mapping(
            "https://dev.azure.com/example", "project", "repo",
            "other-org", "other-repo",
        )
    with pytest.raises(ValueError, match="already mapped from"):
        db.register_repository_mapping(
            "https://dev.azure.com/example", "Other", "Repo",
            "TARGET-ORG", "TARGET-REPO",
        )


def test_plan_lease_prevents_concurrent_runs_for_same_plan(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    first = db.create_pev_run("plan-1", run_id="run-1")
    second = db.create_pev_run("plan-1", run_id="run-2")

    assert db.acquire_pev_plan_lease(
        "plan-1", first, "worker-1", ttl_seconds=300
    )
    assert not db.acquire_pev_plan_lease(
        "plan-1", second, "worker-2", ttl_seconds=300
    )
    assert db.renew_pev_plan_lease(
        "plan-1", first, "worker-1", ttl_seconds=300
    )
    assert db.release_pev_plan_lease("plan-1", first, "worker-1")
    assert db.acquire_pev_plan_lease(
        "plan-1", second, "worker-2", ttl_seconds=300
    )


def test_repository_ownership_requires_run_plan_and_provenance(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_id = db.create_pev_run("plan-1", run_id="run-1")
    db.register_repository_mapping(
        "https://dev.azure.com/example",
        "Project",
        "Repo",
        "target-org",
        "target-repo",
        fingerprint="plan-1",
        status="planned",
    )

    db.record_repository_ownership(
        source_org="https://dev.azure.com/example",
        ado_project="Project",
        ado_repo="Repo",
        gh_org="target-org",
        gh_repo="target-repo",
        plan_id="plan-1",
        run_id=run_id,
        target_repo_id="R_immutable",
    )

    assert db.repository_owned_by_run(
        "target-org",
        "target-repo",
        plan_id="plan-1",
        run_id=run_id,
        target_repo_id="R_immutable",
    )
    assert not db.repository_owned_by_run(
        "target-org",
        "target-repo",
        plan_id="plan-1",
        run_id=run_id,
        target_repo_id="R_replacement",
    )


def test_pev_task_lifecycle_is_idempotent_and_lease_safe(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_id = db.create_pev_run("plan-1", "config-hash", run_id="run-1")
    task_id = db.upsert_pev_task(
        run_id, "migrate_repo", "input-hash", source_ref="ado/P/R",
        target_ref="github/O/R",
    )
    assert task_id == db.upsert_pev_task(
        run_id, "migrate_repo", "input-hash", source_ref="ado/P/R",
        target_ref="github/O/R",
    )

    def claim(worker):
        return db.claim_pev_task(task_id, worker)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(claim, [f"worker-{i}" for i in range(8)]))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0]["attempt"] == 1
    owner = winners[0]["lease_owner"]
    assert not db.update_pev_task(
        task_id, "completed", {"ok": True}, lease_owner="wrong",
        expected_status="in_progress",
    )
    assert db.update_pev_task(
        task_id, "completed", {"ok": True}, lease_owner=owner,
        expected_status="in_progress",
    )
    assert db.get_pev_task(task_id)["status"] == "completed"

    with pytest.raises(ValueError, match="immutable input"):
        db.upsert_pev_task(
            run_id, "migrate_repo", "different-input", task_id=task_id,
            idempotency_key=db.get_pev_task(task_id)["idempotency_key"],
        )


def test_validation_and_llm_audit_store_digests_not_raw_payloads(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_id = db.create_pev_run("plan", run_id="run")
    task_id = db.upsert_pev_task(run_id, "pipeline", "input")
    raw_evidence = {
        "checks": 12,
        "diagnostic": "curl -u build:do-not-persist https://example.invalid",
    }
    evidence_id = db.record_validation_evidence(
        run_id, "workflow-schema", "pass", raw_evidence, task_id,
    )
    assert evidence_id == db.record_validation_evidence(
        run_id, "workflow-schema", "pass", raw_evidence, task_id,
    )
    stored_evidence = db.list_validation_evidence(run_id)
    assert len(stored_evidence) == 1
    assert "do-not-persist" not in stored_evidence[0]["evidence_json"]

    decision_id = db.record_llm_decision(
        run_id, "openai", "model", "prompt-sha", "response-sha",
        reason="ambiguous custom task token=do-not-store", confidence=0.8, task_id=task_id,
        schema_valid=True,
    )
    decision = db.list_llm_decisions(run_id)[0]
    assert decision["decision_id"] == decision_id
    assert decision["prompt_digest"] == "prompt-sha"
    assert "do-not-store" not in decision["reason"]
    with db._conn() as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(llm_decisions)")
        }
    assert "prompt" not in columns
    assert "response" not in columns


def test_pipeline_identity_includes_type_and_yaml_content_is_not_persisted(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    build = _pipeline(PipelineType.YAML, "build")
    release = _pipeline(PipelineType.RELEASE, "release")
    build.variables = [
        PipelineVariable(name="NORMAL", value="visible"),
        PipelineVariable(name="DEPLOY_TOKEN", value="super-secret", is_secret=True),
    ]
    build.service_connections = [{
        "name": "storage",
        "AccountKey": "connection-key-do-not-store",
    }]
    ado_pat = "A" * 75 + "AZDO" + "B" * 5
    build.stages = [PipelineStage(
        name="build",
        jobs=[{
            "job": "build",
            "steps": [{
                "script": (
                    "curl 'https://storage.example/blob?sv=2024&sp=r&sig=SasSecret' "
                    "-H 'Authorization: Basic dXNlcjpwYXNz' "
                    f"-u build:{ado_pat}"
                ),
            }],
        }],
    )]
    db.upsert_pipeline_inventory(build)
    db.upsert_pipeline_inventory(release)

    rows = db.get_all_inventory()
    assert len(rows) == 2
    assert {row["pipeline_type"] for row in rows} == {"yaml", "release"}
    assert all("yaml_content" not in json.loads(row["metadata_json"]) for row in rows)
    build_payload = next(
        json.loads(row["metadata_json"])
        for row in rows if row["pipeline_type"] == "yaml"
    )
    assert build_payload["variables"][0]["value"] == "visible"
    assert build_payload["variables"][1]["value"] == "[REDACTED]"
    stored = json.dumps(build_payload)
    assert "SasSecret" not in stored
    assert "dXNlcjpwYXNz" not in stored
    assert ado_pat not in stored
    assert "connection-key-do-not-store" not in stored
    assert "<redacted>" in stored
    assert rows[0]["source_yaml_sha256"] or rows[1]["source_yaml_sha256"]

    db.upsert_pipeline_migration(
        1, build, "target-org", "repo", MigrationStatus.COMPLETED
    )
    db.upsert_pipeline_migration(
        1, release, "target-org", "repo", MigrationStatus.COMPLETED
    )
    migrations = db.get_wave_pipeline_migrations(1)
    assert len(migrations) == 2
    assert {row["pipeline_type"] for row in migrations} == {"yaml", "release"}


def test_pipeline_receipts_preserve_exact_plan_run_history(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    pipeline = _pipeline(PipelineType.YAML)
    db.upsert_pipeline_migration(
        12,
        pipeline,
        "target-org",
        "repo",
        MigrationStatus.COMPLETED,
        pev_plan_id="plan-1",
        pev_run_id="run-1",
        source_fingerprint="sha256:" + "a" * 64,
    )
    db.upsert_pipeline_migration(
        12,
        pipeline,
        "target-org",
        "repo",
        MigrationStatus.FAILED,
        pev_plan_id="plan-1",
        pev_run_id="run-2",
        source_fingerprint="sha256:" + "a" * 64,
    )

    first = db.get_wave_pipeline_migrations(
        12, pev_plan_id="plan-1", pev_run_id="run-1"
    )
    second = db.get_wave_pipeline_migrations(
        12, pev_plan_id="plan-1", pev_run_id="run-2"
    )
    assert len(first) == len(second) == 1
    assert first[0]["status"] == "completed"
    assert second[0]["status"] == "failed"
    with db._conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM pipeline_migrations WHERE wave_id=12"
        ).fetchone()[0] == 2


def test_wave_run_can_be_completed_by_specific_run_id(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    run_id = db.mark_wave_run(4, "started")
    assert db.mark_wave_run(4, "started") == run_id
    db.mark_wave_run(4, "completed", run_id=run_id)
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM wave_runs WHERE id=?", (run_id,)).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None


def test_pipeline_conversion_attempt_is_append_only(tmp_path):
    db = StateDB(str(tmp_path / "state.db"))
    attempt_id = db.start_pipeline_conversion_attempt(
        1, "Project", 7, "source-hash", pipeline_type="release",
        mode="hybrid", llm_used=True, prompt_digest="prompt-hash",
    )
    assert db.finish_pipeline_conversion_attempt(
        attempt_id, "completed", response_digest="response-hash",
        validation_status="pass", production_ready=True,
        validation_report={"errors": []},
    )
    assert not db.finish_pipeline_conversion_attempt(attempt_id, "failed")
    latest = db.get_latest_pipeline_conversion_attempt(
        1, "Project", 7, "release"
    )
    assert latest["attempt_id"] == attempt_id
    assert latest["production_ready"] == 1
