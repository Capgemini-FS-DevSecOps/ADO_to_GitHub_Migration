"""Durable SQLite state for migrations and Planner/Executor/Validator runs.

The database is intentionally accessed through short-lived connections.  This
keeps the class safe to share between the worker threads used by the migration
runner while SQLite WAL mode provides concurrent readers.  Schema changes are
applied as transactional, additive migrations so state created by older
versions can be opened without a destructive rebuild.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ado2gh.models import (
    BatchCheckpoint, MigrationStatus, PhaseGateResult, PhaseType,
    PipelineMetadata, RepoConfig,
)


@dataclass(frozen=True)
class PipelineInventorySnapshot:
    """Immutable, content-addressed pipeline metadata captured by one SELECT.

    SQLite rows are converted to strings before the connection is released so
    callers never re-read mutable inventory state while executing an approved
    plan.  Raw YAML remains source-side and is verified just-in-time by the
    migration engine against the digest in ``receipts_json``.
    """

    project: str
    repo_name: str
    inventory_digest: str
    receipts_json: str
    pipeline_metadata_json: tuple[str, ...]

    @property
    def pipeline_count(self) -> int:
        return len(self.pipeline_metadata_json)

    def receipts(self) -> list[dict]:
        value = json.loads(self.receipts_json)
        if not isinstance(value, list):  # defensive: instances are DB-created
            raise ValueError("Pipeline snapshot receipts must be a list")
        return value

    def pipelines(self) -> tuple[PipelineMetadata, ...]:
        return tuple(
            PipelineMetadata.from_dict(json.loads(payload))
            for payload in self.pipeline_metadata_json
        )


class StateDB:
    """Thread-safe SQLite persistence with an additive schema upgrade path."""

    CURRENT_SCHEMA_VERSION = 14
    DEFAULT_BUSY_TIMEOUT_MS = 30_000
    _SENSITIVE_NAME = re.compile(
        r"(?:^|[_-])(?:password|passwd|secret|token|private[_-]?key|"
        r"api[_-]?key|client[_-]?secret|credential|authorization|"
        r"account[_-]?key|shared[_-]?access[_-]?key|"
        r"shared[_-]?access[_-]?signature|connection[_-]?string|sas)"
        r"(?:$|[_-])",
        re.IGNORECASE,
    )

    # Version 1 is the schema shipped by ado2gh v5.0.  Keep it intact so an
    # unversioned legacy database can be adopted simply by running its CREATE
    # IF NOT EXISTS statements and recording the version.
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS migrations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id         INTEGER NOT NULL,
        ado_project     TEXT NOT NULL,
        ado_repo        TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        scope           TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        started_at      TEXT,
        completed_at    TEXT,
        error_message   TEXT,
        gh_migration_id TEXT,
        stats           TEXT,
        UNIQUE(wave_id, ado_project, ado_repo, scope)
    );

    CREATE TABLE IF NOT EXISTS wave_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id      INTEGER NOT NULL,
        started_at   TEXT,
        completed_at TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        dry_run      INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS pipeline_inventory (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        pipeline_id     INTEGER NOT NULL,
        pipeline_name   TEXT NOT NULL,
        pipeline_type   TEXT NOT NULL DEFAULT 'yaml',
        repo_id         TEXT NOT NULL DEFAULT '',
        repo_name       TEXT NOT NULL DEFAULT '',
        folder          TEXT NOT NULL DEFAULT '',
        complexity      TEXT NOT NULL DEFAULT 'simple',
        metadata_json   TEXT NOT NULL DEFAULT '{}',
        scanned_at      TEXT,
        UNIQUE(project, pipeline_id)
    );

    CREATE TABLE IF NOT EXISTS pipeline_migrations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id         INTEGER NOT NULL,
        project         TEXT NOT NULL,
        pipeline_id     INTEGER NOT NULL,
        pipeline_name   TEXT NOT NULL,
        repo_name       TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        workflow_file   TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        started_at      TEXT,
        completed_at    TEXT,
        error_message   TEXT,
        warnings        TEXT,
        unsupported_tasks TEXT,
        complexity      TEXT,
        transform_stats TEXT,
        UNIQUE(wave_id, project, pipeline_id)
    );

    CREATE TABLE IF NOT EXISTS repo_risk_scores (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        project        TEXT NOT NULL,
        repo_name      TEXT NOT NULL,
        total_score    REAL NOT NULL DEFAULT 0,
        assigned_phase TEXT,
        gh_org         TEXT NOT NULL DEFAULT '',
        gh_repo        TEXT NOT NULL DEFAULT '',
        score_json     TEXT NOT NULL DEFAULT '{}',
        scored_at      TEXT,
        UNIQUE(project, repo_name)
    );

    CREATE TABLE IF NOT EXISTS phase_gates (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        phase                TEXT NOT NULL,
        status               TEXT,
        repo_success_pct     REAL,
        pipeline_success_pct REAL,
        repos_completed      INTEGER,
        repos_total          INTEGER,
        pipelines_completed  INTEGER,
        pipelines_total      INTEGER,
        failures_json        TEXT,
        override_reason      TEXT,
        checked_at           TEXT,
        UNIQUE(phase)
    );

    CREATE TABLE IF NOT EXISTS batch_checkpoints (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        phase         TEXT NOT NULL,
        batch_num     INTEGER NOT NULL,
        total_batches INTEGER NOT NULL,
        repos_done    INTEGER NOT NULL DEFAULT 0,
        repos_total   INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'pending',
        started_at    TEXT,
        completed_at  TEXT,
        UNIQUE(phase, batch_num)
    );
    """

    # Version 2 adds immutable mapping identities and generic PEV audit state.
    # JSON columns are deliberate: orchestration can evolve its payload schema
    # without coupling the persistence layer to models.py dataclasses.
    PEV_SCHEMA = """
    CREATE TABLE IF NOT EXISTS repository_mappings (
        mapping_id    TEXT PRIMARY KEY,
        source_org    TEXT NOT NULL,
        ado_project   TEXT NOT NULL,
        ado_repo      TEXT NOT NULL,
        gh_org        TEXT NOT NULL,
        gh_repo       TEXT NOT NULL,
        policy        TEXT NOT NULL DEFAULT 'explicit',
        fingerprint   TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'planned',
        source_key    TEXT NOT NULL,
        target_key    TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        UNIQUE(source_key),
        UNIQUE(target_key)
    );

    CREATE TABLE IF NOT EXISTS pev_runs (
        run_id         TEXT PRIMARY KEY,
        plan_id        TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'planned',
        config_digest  TEXT NOT NULL DEFAULT '',
        summary_json   TEXT NOT NULL DEFAULT '{}',
        created_at     TEXT NOT NULL,
        started_at     TEXT,
        completed_at   TEXT,
        updated_at     TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pev_tasks (
        task_id          TEXT PRIMARY KEY,
        run_id           TEXT NOT NULL REFERENCES pev_runs(run_id),
        kind             TEXT NOT NULL,
        source_ref       TEXT NOT NULL DEFAULT '',
        target_ref       TEXT NOT NULL DEFAULT '',
        input_digest     TEXT NOT NULL,
        idempotency_key  TEXT NOT NULL,
        strategy         TEXT NOT NULL DEFAULT 'deterministic',
        dependency_ids   TEXT NOT NULL DEFAULT '[]',
        status           TEXT NOT NULL DEFAULT 'pending',
        attempt          INTEGER NOT NULL DEFAULT 0,
        max_attempts     INTEGER NOT NULL DEFAULT 3,
        lease_owner      TEXT,
        lease_expires_at TEXT,
        not_before       TEXT,
        result_json      TEXT NOT NULL DEFAULT '{}',
        error            TEXT,
        created_at       TEXT NOT NULL,
        started_at       TEXT,
        completed_at     TEXT,
        updated_at       TEXT NOT NULL,
        UNIQUE(run_id, idempotency_key)
    );

    CREATE TABLE IF NOT EXISTS validation_evidence (
        evidence_id     TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL REFERENCES pev_runs(run_id),
        task_id         TEXT REFERENCES pev_tasks(task_id),
        category        TEXT NOT NULL,
        verdict         TEXT NOT NULL,
        evidence_json   TEXT NOT NULL DEFAULT '{}',
        evidence_digest TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE(run_id, task_id, category, evidence_digest)
    );

    CREATE TABLE IF NOT EXISTS llm_decisions (
        decision_id     TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL REFERENCES pev_runs(run_id),
        task_id         TEXT REFERENCES pev_tasks(task_id),
        provider        TEXT NOT NULL,
        model           TEXT NOT NULL,
        prompt_digest   TEXT NOT NULL,
        response_digest TEXT NOT NULL,
        reason          TEXT NOT NULL DEFAULT '',
        confidence      REAL,
        schema_valid    INTEGER,
        policy_status   TEXT NOT NULL DEFAULT '',
        artifact_ref    TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pipeline_conversion_attempts (
        attempt_id            TEXT PRIMARY KEY,
        wave_id               INTEGER NOT NULL,
        project               TEXT NOT NULL,
        pipeline_id           INTEGER NOT NULL,
        plan_id               TEXT NOT NULL DEFAULT '',
        source_fingerprint    TEXT NOT NULL,
        ruleset_version       TEXT NOT NULL DEFAULT '',
        mode                  TEXT NOT NULL DEFAULT 'deterministic',
        llm_used              INTEGER NOT NULL DEFAULT 0,
        provider              TEXT NOT NULL DEFAULT '',
        model                 TEXT NOT NULL DEFAULT '',
        prompt_digest         TEXT NOT NULL DEFAULT '',
        response_digest       TEXT NOT NULL DEFAULT '',
        resolution_count      INTEGER NOT NULL DEFAULT 0,
        validation_status     TEXT NOT NULL DEFAULT 'pending',
        production_ready      INTEGER NOT NULL DEFAULT 0,
        validation_report_json TEXT NOT NULL DEFAULT '{}',
        evidence_file         TEXT NOT NULL DEFAULT '',
        started_at            TEXT NOT NULL,
        finished_at           TEXT,
        status                TEXT NOT NULL DEFAULT 'in_progress',
        error                 TEXT,
        correlation_id        TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_migrations_wave_status
        ON migrations(wave_id, status);
    CREATE INDEX IF NOT EXISTS idx_pipeline_migrations_wave_status
        ON pipeline_migrations(wave_id, status);
    CREATE INDEX IF NOT EXISTS idx_inventory_repo
        ON pipeline_inventory(project, repo_name);
    CREATE INDEX IF NOT EXISTS idx_pev_runs_status
        ON pev_runs(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_pev_tasks_run_status
        ON pev_tasks(run_id, status, created_at);
    CREATE INDEX IF NOT EXISTS idx_validation_run_task
        ON validation_evidence(run_id, task_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_llm_decisions_run_task
        ON llm_decisions(run_id, task_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_pipeline_attempt_latest
        ON pipeline_conversion_attempts(wave_id, project, pipeline_id, started_at);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_wave_runs_one_active
        ON wave_runs(wave_id) WHERE completed_at IS NULL;
    """

    # Azure build and release APIs allocate numeric IDs independently.  The
    # legacy (project, pipeline_id) identity therefore merged unrelated YAML,
    # classic, and release pipelines.  Rebuild the two affected tables with
    # pipeline_type in their natural keys while preserving row IDs and data.
    PIPELINE_IDENTITY_SCHEMA = """
    ALTER TABLE pipeline_inventory RENAME TO pipeline_inventory_legacy;
    CREATE TABLE pipeline_inventory (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        pipeline_id     INTEGER NOT NULL,
        pipeline_name   TEXT NOT NULL,
        pipeline_type   TEXT NOT NULL DEFAULT 'yaml',
        repo_id         TEXT NOT NULL DEFAULT '',
        repo_name       TEXT NOT NULL DEFAULT '',
        folder          TEXT NOT NULL DEFAULT '',
        complexity      TEXT NOT NULL DEFAULT 'simple',
        metadata_json   TEXT NOT NULL DEFAULT '{}',
        scanned_at      TEXT,
        UNIQUE(project, pipeline_id, pipeline_type)
    );
    INSERT INTO pipeline_inventory
        (id,project,pipeline_id,pipeline_name,pipeline_type,repo_id,repo_name,
         folder,complexity,metadata_json,scanned_at)
    SELECT id,project,pipeline_id,pipeline_name,pipeline_type,repo_id,repo_name,
           folder,complexity,metadata_json,scanned_at
    FROM pipeline_inventory_legacy;
    DROP TABLE pipeline_inventory_legacy;

    ALTER TABLE pipeline_migrations RENAME TO pipeline_migrations_legacy;
    CREATE TABLE pipeline_migrations (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id           INTEGER NOT NULL,
        project           TEXT NOT NULL,
        pipeline_id       INTEGER NOT NULL,
        pipeline_type     TEXT NOT NULL DEFAULT 'yaml',
        pipeline_name     TEXT NOT NULL,
        repo_name         TEXT NOT NULL,
        gh_org            TEXT NOT NULL,
        gh_repo           TEXT NOT NULL,
        workflow_file     TEXT,
        status            TEXT NOT NULL DEFAULT 'pending',
        started_at        TEXT,
        completed_at      TEXT,
        error_message     TEXT,
        warnings          TEXT,
        unsupported_tasks TEXT,
        complexity        TEXT,
        transform_stats   TEXT,
        UNIQUE(wave_id, project, pipeline_id, pipeline_type)
    );
    INSERT INTO pipeline_migrations
        (id,wave_id,project,pipeline_id,pipeline_type,pipeline_name,repo_name,
         gh_org,gh_repo,workflow_file,status,started_at,completed_at,
         error_message,warnings,unsupported_tasks,complexity,transform_stats)
    SELECT pm.id,pm.wave_id,pm.project,pm.pipeline_id,
           COALESCE((SELECT pi.pipeline_type FROM pipeline_inventory pi
                     WHERE pi.project=pm.project
                       AND pi.pipeline_id=pm.pipeline_id LIMIT 1),'yaml'),
           pm.pipeline_name,pm.repo_name,pm.gh_org,pm.gh_repo,pm.workflow_file,
           pm.status,pm.started_at,pm.completed_at,pm.error_message,pm.warnings,
           pm.unsupported_tasks,pm.complexity,pm.transform_stats
    FROM pipeline_migrations_legacy pm;
    DROP TABLE pipeline_migrations_legacy;

    DROP INDEX IF EXISTS idx_pipeline_attempt_latest;
    ALTER TABLE pipeline_conversion_attempts
        ADD COLUMN pipeline_type TEXT NOT NULL DEFAULT 'yaml';
    CREATE INDEX IF NOT EXISTS idx_migrations_wave_status
        ON migrations(wave_id, status);
    CREATE INDEX IF NOT EXISTS idx_pipeline_migrations_wave_status
        ON pipeline_migrations(wave_id, status);
    CREATE INDEX IF NOT EXISTS idx_inventory_repo
        ON pipeline_inventory(project, repo_name);
    CREATE INDEX IF NOT EXISTS idx_pipeline_attempt_latest
        ON pipeline_conversion_attempts(
            wave_id, project, pipeline_id, pipeline_type, started_at
        );
    """

    PIPELINE_FINGERPRINT_SCHEMA = """
    ALTER TABLE pipeline_inventory
        ADD COLUMN source_yaml_path TEXT NOT NULL DEFAULT '';
    ALTER TABLE pipeline_inventory
        ADD COLUMN source_yaml_sha256 TEXT NOT NULL DEFAULT '';
    ALTER TABLE pipeline_inventory
        ADD COLUMN metadata_schema_version INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE pipeline_inventory
        ADD COLUMN inventory_ruleset_version TEXT NOT NULL DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_inventory_source_fingerprint
        ON pipeline_inventory(project, pipeline_id, pipeline_type,
                              source_yaml_sha256);
    """

    INVENTORY_RUN_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pipeline_inventory_runs (
        run_id          TEXT PRIMARY KEY,
        project         TEXT NOT NULL,
        status          TEXT NOT NULL,
        include_releases INTEGER NOT NULL DEFAULT 1,
        build_count     INTEGER NOT NULL DEFAULT 0,
        release_count   INTEGER NOT NULL DEFAULT 0,
        total_count     INTEGER NOT NULL DEFAULT 0,
        failed_count    INTEGER NOT NULL DEFAULT 0,
        unmapped_count  INTEGER NOT NULL DEFAULT 0,
        ruleset_version TEXT NOT NULL DEFAULT '',
        error_message   TEXT,
        started_at      TEXT NOT NULL,
        completed_at    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_pipeline_inventory_run_latest
        ON pipeline_inventory_runs(project, started_at DESC);

    CREATE TABLE IF NOT EXISTS migration_expectations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id     INTEGER NOT NULL,
        ado_project TEXT NOT NULL,
        ado_repo    TEXT NOT NULL,
        gh_org      TEXT NOT NULL,
        gh_repo     TEXT NOT NULL,
        scope       TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        UNIQUE(wave_id, ado_project, ado_repo, scope)
    );
    CREATE INDEX IF NOT EXISTS idx_migration_expectation_source
        ON migration_expectations(ado_project, ado_repo, scope);
    """

    # Version 6 separates planning intent from destructive ownership proof and
    # adds a cross-process lease for one active executor per immutable plan.
    # A target name is never ownership: GitHub's immutable repository id, the
    # creating PEV run, and its plan are all required for rollback.
    ENTERPRISE_CONTROL_SCHEMA = """
    ALTER TABLE repository_mappings
        ADD COLUMN ownership_plan_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE repository_mappings
        ADD COLUMN ownership_run_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE repository_mappings
        ADD COLUMN target_repo_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE repository_mappings
        ADD COLUMN ownership_recorded_at TEXT;
    ALTER TABLE repository_mappings
        ADD COLUMN ownership_provenance_digest TEXT NOT NULL DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_repository_mapping_owner
        ON repository_mappings(ownership_plan_id,ownership_run_id,target_repo_id);

    CREATE TABLE IF NOT EXISTS pev_plan_leases (
        plan_id          TEXT PRIMARY KEY,
        run_id           TEXT NOT NULL REFERENCES pev_runs(run_id),
        lease_owner      TEXT NOT NULL,
        lease_expires_at TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    );
    """

    # Version 7 binds pipeline receipts to the exact PEV plan/run and adds
    # cross-plan, per-target fencing.  A plan lease alone is insufficient:
    # two different approved plans can otherwise write the same repository.
    EXECUTION_FENCING_SCHEMA = """
    ALTER TABLE pipeline_migrations
        ADD COLUMN pev_plan_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE pipeline_migrations
        ADD COLUMN pev_run_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE pipeline_migrations
        ADD COLUMN source_fingerprint TEXT NOT NULL DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_pipeline_migration_pev_receipt
        ON pipeline_migrations(
            pev_plan_id,pev_run_id,project,repo_name,pipeline_id,pipeline_type
        );

    CREATE TABLE IF NOT EXISTS pev_target_leases (
        target_key       TEXT PRIMARY KEY,
        gh_org           TEXT NOT NULL,
        gh_repo          TEXT NOT NULL,
        plan_id          TEXT NOT NULL,
        run_id           TEXT NOT NULL REFERENCES pev_runs(run_id),
        lease_owner      TEXT NOT NULL,
        fencing_token    INTEGER NOT NULL,
        lease_expires_at TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pev_target_lease_run
        ON pev_target_leases(plan_id,run_id,lease_owner);
    """

    # Version 8 makes the exact PEV plan/run part of the pipeline receipt's
    # natural key. Version 7 stored those fields but a later run of the same
    # content-addressed plan still overwrote the earlier run's audit receipt.
    PIPELINE_RECEIPT_HISTORY_SCHEMA = """
    DROP INDEX IF EXISTS idx_pipeline_migrations_wave_status;
    DROP INDEX IF EXISTS idx_pipeline_migration_pev_receipt;
    ALTER TABLE pipeline_migrations RENAME TO pipeline_migrations_v7;
    CREATE TABLE pipeline_migrations (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_id            INTEGER NOT NULL,
        project            TEXT NOT NULL,
        pipeline_id        INTEGER NOT NULL,
        pipeline_type      TEXT NOT NULL DEFAULT 'yaml',
        pipeline_name      TEXT NOT NULL,
        repo_name          TEXT NOT NULL,
        gh_org             TEXT NOT NULL,
        gh_repo            TEXT NOT NULL,
        workflow_file      TEXT,
        status             TEXT NOT NULL DEFAULT 'pending',
        started_at         TEXT,
        completed_at       TEXT,
        error_message      TEXT,
        warnings           TEXT,
        unsupported_tasks  TEXT,
        complexity         TEXT,
        transform_stats    TEXT,
        pev_plan_id        TEXT NOT NULL DEFAULT '',
        pev_run_id         TEXT NOT NULL DEFAULT '',
        source_fingerprint TEXT NOT NULL DEFAULT '',
        UNIQUE(
            wave_id,project,pipeline_id,pipeline_type,pev_plan_id,pev_run_id
        )
    );
    INSERT INTO pipeline_migrations (
        id,wave_id,project,pipeline_id,pipeline_type,pipeline_name,repo_name,
        gh_org,gh_repo,workflow_file,status,started_at,completed_at,
        error_message,warnings,unsupported_tasks,complexity,transform_stats,
        pev_plan_id,pev_run_id,source_fingerprint
    ) SELECT
        id,wave_id,project,pipeline_id,pipeline_type,pipeline_name,repo_name,
        gh_org,gh_repo,workflow_file,status,started_at,completed_at,
        error_message,warnings,unsupported_tasks,complexity,transform_stats,
        pev_plan_id,pev_run_id,source_fingerprint
    FROM pipeline_migrations_v7;
    DROP TABLE pipeline_migrations_v7;
    CREATE INDEX idx_pipeline_migrations_wave_status
        ON pipeline_migrations(wave_id,status);
    CREATE INDEX idx_pipeline_migration_pev_receipt
        ON pipeline_migrations(
            pev_plan_id,pev_run_id,project,repo_name,pipeline_id,pipeline_type
        );
    """

    # Version 9 records every run that begins writing an existing target. The
    # original creation receipt remains immutable, but an older run may not
    # delete a repository after a later approved run has reused it.
    TARGET_USE_HISTORY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS repository_target_uses (
        use_id         TEXT PRIMARY KEY,
        target_key     TEXT NOT NULL,
        gh_org         TEXT NOT NULL,
        gh_repo        TEXT NOT NULL,
        plan_id        TEXT NOT NULL,
        run_id         TEXT NOT NULL REFERENCES pev_runs(run_id),
        target_repo_id TEXT NOT NULL,
        status         TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        UNIQUE(target_key,plan_id,run_id)
    );
    CREATE INDEX idx_repository_target_use_history
        ON repository_target_uses(target_key,created_at,plan_id,run_id);
    """

    # Version 10 persists a crash-stop barrier *before* a remote mutation is
    # launched.  A finite worker lease is insufficient for server-side jobs:
    # the host can die after dispatch while GitHub continues mutating.  Active
    # rows block all future target lease acquisitions until the exact
    # operation reaches a known terminal state or an operator reconciles it.
    REMOTE_OPERATION_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pev_remote_operations (
        operation_id    TEXT PRIMARY KEY,
        target_key      TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        plan_id         TEXT NOT NULL,
        run_id          TEXT NOT NULL REFERENCES pev_runs(run_id),
        lease_owner     TEXT NOT NULL,
        fencing_token   INTEGER NOT NULL,
        operation_kind  TEXT NOT NULL,
        operation_digest TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'in_flight',
        resolution_digest TEXT NOT NULL DEFAULT '',
        started_at      TEXT NOT NULL,
        resolved_at     TEXT
    );
    CREATE UNIQUE INDEX idx_pev_remote_operation_active_target
        ON pev_remote_operations(target_key) WHERE status='in_flight';
    CREATE INDEX idx_pev_remote_operation_run
        ON pev_remote_operations(plan_id,run_id,started_at);
    """

    # Version 11 replaces the collision-prone legacy wave projection as the
    # authority for PEV scope resume.  Rows are bound to an exact immutable
    # plan, run, target mapping, and task input digest.  Legacy migrations are
    # retained only for backwards-compatible reports and non-agent commands.
    PEV_SCOPE_RECEIPT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pev_scope_expectations (
        expectation_id TEXT PRIMARY KEY,
        plan_id         TEXT NOT NULL,
        run_id          TEXT NOT NULL REFERENCES pev_runs(run_id),
        wave_id         INTEGER NOT NULL,
        source_key      TEXT NOT NULL,
        target_key      TEXT NOT NULL,
        ado_project     TEXT NOT NULL,
        ado_repo        TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        scope           TEXT NOT NULL,
        input_digest    TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE(plan_id,run_id,source_key,scope)
    );
    CREATE TABLE IF NOT EXISTS pev_scope_receipts (
        receipt_id      TEXT PRIMARY KEY,
        plan_id         TEXT NOT NULL,
        run_id          TEXT NOT NULL REFERENCES pev_runs(run_id),
        wave_id         INTEGER NOT NULL,
        source_key      TEXT NOT NULL,
        target_key      TEXT NOT NULL,
        ado_project     TEXT NOT NULL,
        ado_repo        TEXT NOT NULL,
        gh_org          TEXT NOT NULL,
        gh_repo         TEXT NOT NULL,
        scope           TEXT NOT NULL,
        input_digest    TEXT NOT NULL,
        status          TEXT NOT NULL,
        stats_json      TEXT NOT NULL DEFAULT '{}',
        error           TEXT,
        started_at      TEXT,
        completed_at    TEXT,
        updated_at      TEXT NOT NULL,
        UNIQUE(plan_id,run_id,source_key,scope)
    );
    CREATE INDEX idx_pev_scope_receipt_exact
        ON pev_scope_receipts(plan_id,run_id,status,source_key,scope);
    """

    # Version 12 stores the immutable write capabilities derived from the
    # approved plan task graph. Target leases and scope receipts must be a
    # subset of this manifest; possessing an unrelated run id is not enough.
    PEV_PLAN_CAPABILITY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pev_plan_capabilities (
        plan_id       TEXT NOT NULL,
        source_key    TEXT NOT NULL,
        target_key    TEXT NOT NULL,
        gh_org        TEXT NOT NULL,
        gh_repo       TEXT NOT NULL,
        scope         TEXT NOT NULL,
        input_digest  TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        PRIMARY KEY(plan_id,source_key,scope)
    );
    CREATE INDEX idx_pev_plan_capability_target
        ON pev_plan_capabilities(plan_id,target_key);
    """

    # Version 13 authorizes high-impact operations with a one-shot,
    # content-addressed capability.  The exact canonical request is bound to
    # one immutable plan/run and must be claimed atomically before use.  Every
    # remote mutation gets its own durable before/after receipt so a process
    # crash is distinguishable from a never-started action.
    PEV_DESTRUCTIVE_CAPABILITY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pev_destructive_capabilities (
        capability_id       TEXT PRIMARY KEY,
        plan_id             TEXT NOT NULL,
        run_id              TEXT NOT NULL REFERENCES pev_runs(run_id),
        operation_kind      TEXT NOT NULL,
        request_json        TEXT NOT NULL,
        request_digest      TEXT NOT NULL,
        approval_json       TEXT NOT NULL,
        approval_digest     TEXT NOT NULL,
        status              TEXT NOT NULL,
        claimant            TEXT,
        claim_token         TEXT,
        authorized_at       TEXT NOT NULL,
        claimed_at          TEXT,
        finished_at         TEXT,
        error               TEXT,
        UNIQUE(plan_id,run_id,operation_kind,request_digest)
    );
    CREATE INDEX idx_pev_destructive_capability_run
        ON pev_destructive_capabilities(plan_id,run_id,status,operation_kind);

    CREATE TABLE IF NOT EXISTS pev_destructive_action_receipts (
        receipt_id          TEXT PRIMARY KEY,
        capability_id       TEXT NOT NULL
            REFERENCES pev_destructive_capabilities(capability_id),
        plan_id             TEXT NOT NULL,
        run_id              TEXT NOT NULL,
        operation_kind      TEXT NOT NULL,
        action_key          TEXT NOT NULL,
        source_key          TEXT NOT NULL,
        target_key          TEXT NOT NULL,
        action_kind         TEXT NOT NULL,
        before_json         TEXT NOT NULL,
        before_digest       TEXT NOT NULL,
        after_json          TEXT,
        after_digest        TEXT,
        status              TEXT NOT NULL,
        started_at          TEXT NOT NULL,
        completed_at        TEXT,
        error               TEXT,
        UNIQUE(capability_id,action_key)
    );
    CREATE INDEX idx_pev_destructive_action_capability
        ON pev_destructive_action_receipts(capability_id,status,started_at);
    """

    # Version 14 makes externally signed operational approvals one-shot across
    # processes and restarts.  A nonce is globally unique because reuse under
    # a different action/resource is still a replay, not a new authority.
    GOVERNANCE_NONCE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pev_governance_nonces (
        nonce             TEXT PRIMARY KEY,
        action            TEXT NOT NULL,
        resource_digest   TEXT NOT NULL,
        plan_id           TEXT NOT NULL,
        run_id            TEXT NOT NULL,
        subject           TEXT NOT NULL,
        key_id            TEXT NOT NULL,
        ticket            TEXT NOT NULL,
        envelope_digest   TEXT NOT NULL,
        evidence_json     TEXT NOT NULL,
        claimed_at        TEXT NOT NULL
    );
    CREATE INDEX idx_pev_governance_nonce_plan
        ON pev_governance_nonces(plan_id,run_id,action,claimed_at);
    """

    MIGRATIONS = {
        1: SCHEMA,
        2: PEV_SCHEMA,
        3: PIPELINE_IDENTITY_SCHEMA,
        4: PIPELINE_FINGERPRINT_SCHEMA,
        5: INVENTORY_RUN_SCHEMA,
        6: ENTERPRISE_CONTROL_SCHEMA,
        7: EXECUTION_FENCING_SCHEMA,
        8: PIPELINE_RECEIPT_HISTORY_SCHEMA,
        9: TARGET_USE_HISTORY_SCHEMA,
        10: REMOTE_OPERATION_SCHEMA,
        11: PEV_SCOPE_RECEIPT_SCHEMA,
        12: PEV_PLAN_CAPABILITY_SCHEMA,
        13: PEV_DESTRUCTIVE_CAPABILITY_SCHEMA,
        14: GOVERNANCE_NONCE_SCHEMA,
    }

    def __init__(self, db_path: str = "migration_state.db",
                 timeout_sec: float = 30.0):
        self.db_path = str(db_path)
        self.timeout_sec = float(timeout_sec)
        if self.timeout_sec <= 0:
            raise ValueError("timeout_sec must be greater than zero")
        self._uri = False
        self._keeper_conn: Optional[sqlite3.Connection] = None

        # A fresh connection to ':memory:' normally creates a fresh database,
        # which is incompatible with this class's connection-per-operation
        # design.  A private shared-cache URI plus a keeper connection preserves
        # the expected in-memory semantics (particularly useful to embedders).
        if self.db_path == ":memory:":
            self.db_path = f"file:ado2gh-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._keeper_conn = self._new_conn()
        self._init_db()

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _json_dumps(value) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)

    @staticmethod
    def _digest(value) -> str:
        if not isinstance(value, str):
            value = StateDB._json_dumps(value)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _redact_sensitive_payload(cls, value):
        """Remove credential-like values from normalized inventory metadata."""
        if isinstance(value, (list, tuple)):
            return [cls._redact_sensitive_payload(item) for item in value]
        if isinstance(value, str):
            # Scripts and URLs can carry credentials even when their enclosing
            # key is innocuous (for example ``script`` or ``endpoint``).
            from ado2gh.pipelines.llm import redact_text_secrets
            return redact_text_secrets(value)
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text not in {"is_secret", "isSecret"} \
                    and cls._SENSITIVE_NAME.search(key_text):
                result[key] = "[REDACTED]"
            else:
                result[key] = cls._redact_sensitive_payload(item)
        # PipelineVariable serializes its sensitivity beside the value rather
        # than in the key name.  Also treat credential-like variable names as
        # sensitive because YAML variables have no is_secret flag.
        name = str(value.get("name", ""))
        if "value" in result and (
            bool(value.get("is_secret") or value.get("isSecret"))
            or cls._SENSITIVE_NAME.search(name)
        ):
            result["value"] = "[REDACTED]"
            result["value_redacted"] = True
        return result

    @staticmethod
    def _statements(script: str):
        """Yield complete SQLite statements without implicit commits."""
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                statement = pending.strip()
                pending = ""
                if statement:
                    yield statement
        if pending.strip():
            raise RuntimeError("incomplete SQL statement in schema migration")

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.timeout_sec,
            check_same_thread=False,
            uri=self._uri,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.timeout_sec * 1000)}")
        conn.execute("PRAGMA foreign_keys=ON")
        # Fencing, ownership, and validation receipts must survive a host
        # power loss once commit returns. WAL+NORMAL can lose the most recent
        # commits, which is unsafe when a remote GitHub job keeps running.
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _conn(self) -> sqlite3.Connection:
        return self._new_conn()

    def _init_db(self):
        conn = self._new_conn()
        try:
            # journal_mode persists for a file database and therefore only
            # needs to be negotiated at initialization, not on every worker
            # connection (where it can itself cause lock contention).
            if not self._uri:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)"
            )
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current > self.CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {current} is newer than supported "
                    f"version {self.CURRENT_SCHEMA_VERSION}"
                )
            for version in range(current + 1, self.CURRENT_SCHEMA_VERSION + 1):
                if version == 2:
                    # Older releases could leave more than one unfinished run
                    # for a wave.  Preserve the newest resumable run and close
                    # older duplicates before installing the partial unique
                    # index that makes this invariant race-safe.
                    now = self._utcnow()
                    conn.execute(
                        "UPDATE wave_runs SET completed_at=?, status='interrupted' "
                        "WHERE completed_at IS NULL AND id NOT IN "
                        "(SELECT MAX(id) FROM wave_runs WHERE completed_at IS NULL "
                        " GROUP BY wave_id)",
                        (now,),
                    )
                for statement in self._statements(self.MIGRATIONS[version]):
                    conn.execute(statement)
                now = self._utcnow()
                conn.execute(
                    "INSERT INTO schema_meta(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value, updated_at=excluded.updated_at",
                    ("schema_version", str(version), now),
                )
                conn.execute(f"PRAGMA user_version={version}")
            conn.execute(
                "INSERT INTO schema_meta(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                ("schema_version", str(self.CURRENT_SCHEMA_VERSION), self._utcnow()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @property
    def schema_version(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def transaction(self, immediate: bool = False):
        """Yield one atomic connection and commit or roll back as a unit.

        ``immediate=True`` obtains the write reservation before reading.  Use
        it for read/validate/write operations such as mapping registration and
        task claiming, where a deferred transaction would permit races.
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self) -> None:
        if self._keeper_conn is not None:
            self._keeper_conn.close()
            self._keeper_conn = None

    # ── Repository mapping registry ────────────────────────────────────────

    @staticmethod
    def _required_text(value, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        text = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError(f"{field} must not contain control characters")
        return text

    @classmethod
    def _mapping_key(cls, *parts: str) -> str:
        # GitHub and Azure DevOps resource lookups are case-insensitive.  Store
        # an explicit canonical key rather than relying on SQLite NOCASE, which
        # only implements ASCII case folding.
        return "\x1f".join(cls._required_text(part, "mapping component")
                            .rstrip("/").casefold() for part in parts)

    def register_repository_mapping(
        self,
        source_org: str,
        ado_project: str,
        ado_repo: str,
        gh_org: str,
        gh_repo: str,
        policy: str = "explicit",
        status: str = "planned",
        mapping_id: str = None,
        fingerprint: str = None,
    ) -> str:
        """Register one collision-free source-to-target repository mapping.

        Re-registering the same source and target is idempotent.  Remapping a
        source or attempting to reuse a GitHub destination raises ``ValueError``
        instead of silently redirecting an in-flight migration.
        """
        source_org = self._required_text(source_org, "source_org").rstrip("/")
        ado_project = self._required_text(ado_project, "ado_project")
        ado_repo = self._required_text(ado_repo, "ado_repo")
        gh_org = self._required_text(gh_org, "gh_org")
        gh_repo = self._required_text(gh_repo, "gh_repo")
        policy = self._required_text(policy, "policy")
        status = self._required_text(status, "status")
        source_key = self._mapping_key(source_org, ado_project, ado_repo)
        target_key = self._mapping_key(gh_org, gh_repo)
        canonical = {
            "source_org": source_org,
            "ado_project": ado_project,
            "ado_repo": ado_repo,
            "gh_org": gh_org,
            "gh_repo": gh_repo,
            "policy": policy,
        }
        fingerprint = fingerprint or self._digest(canonical)
        fingerprint = self._required_text(fingerprint, "fingerprint")
        mapping_id = mapping_id or f"map_{self._digest(source_key)[:32]}"
        mapping_id = self._required_text(mapping_id, "mapping_id")
        now = self._utcnow()

        with self.transaction(immediate=True) as conn:
            by_source = conn.execute(
                "SELECT * FROM repository_mappings WHERE source_key=?",
                (source_key,),
            ).fetchone()
            by_target = conn.execute(
                "SELECT * FROM repository_mappings WHERE target_key=?",
                (target_key,),
            ).fetchone()
            if by_source:
                if by_source["target_key"] != target_key:
                    raise ValueError(
                        "source repository is already mapped to "
                        f"{by_source['gh_org']}/{by_source['gh_repo']}"
                    )
                if mapping_id != by_source["mapping_id"]:
                    raise ValueError(
                        f"source repository already has mapping_id "
                        f"{by_source['mapping_id']}"
                    )
                # Planning is intent, not ownership.  Never let a later plan
                # downgrade or commandeer an adopted/owned target merely by
                # rediscovering the same name.  A rolled-back mapping can be
                # planned again because immutable target-id verification will
                # distinguish any replacement repository.
                protected = {"adopted", "created", "migrated"}
                current_status = str(by_source["status"])
                if current_status in protected:
                    status = current_status
                    fingerprint = str(by_source["fingerprint"])
                conn.execute(
                    "UPDATE repository_mappings SET policy=?, fingerprint=?, "
                    "status=?, updated_at=? WHERE mapping_id=?",
                    (policy, fingerprint, status, now, mapping_id),
                )
                return mapping_id
            if by_target:
                raise ValueError(
                    f"GitHub target {gh_org}/{gh_repo} is already mapped from "
                    f"{by_target['source_org']}/{by_target['ado_project']}/"
                    f"{by_target['ado_repo']}"
                )
            conn.execute(
                "INSERT INTO repository_mappings "
                "(mapping_id,source_org,ado_project,ado_repo,gh_org,gh_repo,"
                " policy,fingerprint,status,source_key,target_key,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mapping_id, source_org, ado_project, ado_repo, gh_org, gh_repo,
                 policy, fingerprint, status, source_key, target_key, now, now),
            )
        return mapping_id

    # Alias communicates the idempotent behavior to orchestration code.
    upsert_repository_mapping = register_repository_mapping

    def record_repository_ownership(
        self,
        *,
        source_org: str,
        ado_project: str,
        ado_repo: str,
        gh_org: str,
        gh_repo: str,
        plan_id: str,
        run_id: str,
        target_repo_id: str,
        status: str = "created",
    ) -> None:
        """Record proof that one PEV run created a specific GitHub repo.

        This transition is deliberately separate from mapping registration.
        The run must already be bound to the plan, the mapping must still be
        bound to that plan, and adopted targets can never acquire ownership by
        calling this method.
        """
        source_org = self._required_text(source_org, "source_org").rstrip("/")
        ado_project = self._required_text(ado_project, "ado_project")
        ado_repo = self._required_text(ado_repo, "ado_repo")
        gh_org = self._required_text(gh_org, "gh_org")
        gh_repo = self._required_text(gh_repo, "gh_repo")
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        target_repo_id = self._required_text(target_repo_id, "target_repo_id")
        status = self._required_text(status, "status")
        if status not in {"created", "migrated"}:
            raise ValueError("ownership status must be created or migrated")
        source_key = self._mapping_key(source_org, ado_project, ado_repo)
        target_key = self._mapping_key(gh_org, gh_repo)
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("ownership run is not bound to the approved plan")
            mapping = conn.execute(
                "SELECT * FROM repository_mappings WHERE source_key=?",
                (source_key,),
            ).fetchone()
            if not mapping or mapping["target_key"] != target_key:
                raise ValueError("repository mapping does not match the created target")
            current = str(mapping["status"])
            if current == "adopted":
                raise PermissionError("an adopted target cannot become agent-owned")
            if current in {"created", "migrated"}:
                same_owner = (
                    mapping["ownership_plan_id"] == plan_id
                    and mapping["ownership_run_id"] == run_id
                    and mapping["target_repo_id"] == target_repo_id
                )
                if not same_owner:
                    raise PermissionError(
                        "repository already has a different ownership receipt"
                    )
            elif current not in {"planned", "unverified", "rolled_back"}:
                raise PermissionError(
                    f"mapping state {current!r} cannot transition to ownership"
                )
            if mapping["fingerprint"] != plan_id and current not in {
                "created", "migrated"
            }:
                raise ValueError("repository mapping is not bound to this plan")
            conn.execute(
                "UPDATE repository_mappings SET status=?,ownership_plan_id=?,"
                "ownership_run_id=?,target_repo_id=?,ownership_recorded_at=?,"
                "ownership_provenance_digest=?,updated_at=? WHERE mapping_id=?",
                (
                    status, plan_id, run_id, target_repo_id, now,
                    self._digest({
                        "mapping_id": mapping["mapping_id"],
                        "plan_id": plan_id,
                        "run_id": run_id,
                        "target_repo_id": target_repo_id,
                    }),
                    now,
                    mapping["mapping_id"],
                ),
            )

    def repository_owned_by_run(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        plan_id: str,
        run_id: str,
        target_repo_id: str = "",
    ) -> bool:
        target_key = self._mapping_key(gh_org, gh_repo)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mapping_id,status,ownership_plan_id,ownership_run_id,"
                "target_repo_id,ownership_provenance_digest "
                "FROM repository_mappings WHERE target_key=?", (target_key,)
            ).fetchone()
        if not row or row["status"] not in {"created", "migrated"}:
            return False
        if row["ownership_plan_id"] != plan_id or row["ownership_run_id"] != run_id:
            return False
        expected_provenance = self._digest({
            "mapping_id": row["mapping_id"],
            "plan_id": row["ownership_plan_id"],
            "run_id": row["ownership_run_id"],
            "target_repo_id": row["target_repo_id"],
        })
        if row["ownership_provenance_digest"] != expected_provenance:
            return False
        return not target_repo_id or row["target_repo_id"] == str(target_repo_id)

    def record_repository_target_use(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        plan_id: str,
        run_id: str,
        target_repo_id: str,
        status: str = "executing",
    ) -> str:
        """Append the exact PEV run that began operating on a target.

        Creation ownership is intentionally immutable, so it cannot express
        that a later approved run reused the repository.  This separate
        history closes the otherwise-dangerous case where an old run could
        roll back (delete) a repository after a newer run had modified it.
        """
        gh_org = self._required_text(gh_org, "gh_org")
        gh_repo = self._required_text(gh_repo, "gh_repo")
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        target_repo_id = self._required_text(target_repo_id, "target_repo_id")
        status = self._required_text(status, "status")
        target_key = self._mapping_key(gh_org, gh_repo)
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("target-use run is not bound to the approved plan")
            existing = conn.execute(
                "SELECT use_id,target_repo_id FROM repository_target_uses "
                "WHERE target_key=? AND plan_id=? AND run_id=?",
                (target_key, plan_id, run_id),
            ).fetchone()
            if existing:
                if existing["target_repo_id"] != target_repo_id:
                    raise PermissionError(
                        "target-use receipt cannot be rebound to a replacement "
                        "repository id"
                    )
                conn.execute(
                    "UPDATE repository_target_uses SET status=?,updated_at=? "
                    "WHERE use_id=?",
                    (status, now, existing["use_id"]),
                )
                return str(existing["use_id"])
            use_id = f"target_use_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO repository_target_uses "
                "(use_id,target_key,gh_org,gh_repo,plan_id,run_id," 
                "target_repo_id,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    use_id, target_key, gh_org, gh_repo, plan_id, run_id,
                    target_repo_id, status, now, now,
                ),
            )
            return use_id

    def list_repository_target_uses(
        self, gh_org: str, gh_repo: str
    ) -> list[dict]:
        target_key = self._mapping_key(gh_org, gh_repo)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM repository_target_uses WHERE target_key=? "
                "ORDER BY created_at,use_id",
                (target_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def repository_has_foreign_target_use(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        owner_plan_id: str,
        owner_run_id: str,
        target_repo_id: str,
    ) -> bool:
        """Return true once any other run has used the owned repository.

        This is deliberately conservative.  A cascading rollback must be an
        explicit operator workflow because deleting the original repository
        would also delete artifacts produced by every subsequent consumer.
        """
        target_key = self._mapping_key(gh_org, gh_repo)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM repository_target_uses WHERE target_key=? "
                "AND target_repo_id=? AND NOT (plan_id=? AND run_id=?) LIMIT 1",
                (
                    target_key, target_repo_id, owner_plan_id, owner_run_id,
                ),
            ).fetchone()
        return row is not None

    def mark_repository_rolled_back(
        self, gh_org: str, gh_repo: str, *, plan_id: str, run_id: str
    ) -> bool:
        target_key = self._mapping_key(gh_org, gh_repo)
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE repository_mappings SET status='rolled_back',updated_at=? "
                "WHERE target_key=? AND status IN ('created','migrated') "
                "AND ownership_plan_id=? AND ownership_run_id=?",
                (self._utcnow(), target_key, plan_id, run_id),
            )
            return cur.rowcount == 1

    def get_repository_mapping(
        self,
        mapping_id: str = None,
        *,
        source_org: str = None,
        ado_project: str = None,
        ado_repo: str = None,
    ) -> Optional[dict]:
        with self._conn() as conn:
            if mapping_id is not None:
                row = conn.execute(
                    "SELECT * FROM repository_mappings WHERE mapping_id=?",
                    (mapping_id,),
                ).fetchone()
            else:
                if None in (source_org, ado_project, ado_repo):
                    raise ValueError(
                        "provide mapping_id or source_org, ado_project, and ado_repo"
                    )
                source_key = self._mapping_key(
                    source_org, ado_project, ado_repo
                )
                row = conn.execute(
                    "SELECT * FROM repository_mappings WHERE source_key=?",
                    (source_key,),
                ).fetchone()
            return dict(row) if row else None

    def list_repository_mappings(self, status: str = None) -> list[dict]:
        with self._conn() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM repository_mappings ORDER BY created_at,mapping_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM repository_mappings WHERE status=? "
                    "ORDER BY created_at,mapping_id", (status,)
                ).fetchall()
            return [dict(row) for row in rows]

    # ── Generic Planner / Executor / Validator state ───────────────────────

    def register_pev_plan_capabilities(
        self, plan_id: str, capabilities: list[dict]
    ) -> None:
        """Persist the exact target/scope write manifest for one plan.

        Re-registration is allowed only when the complete canonical set is
        byte-for-byte equivalent. This method is called at the explicit plan
        approval boundary before any run or target lease is created.
        """
        plan_id = self._required_text(plan_id, "plan_id")
        if not capabilities:
            raise ValueError("approved plan capability manifest cannot be empty")
        normalized: list[tuple[str, str, str, str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(capabilities):
            if not isinstance(item, dict):
                raise ValueError(f"capability {index} must be an object")
            source_key = self._required_text(item.get("source_key"), "source_key")
            gh_org = self._required_text(item.get("gh_org"), "gh_org")
            gh_repo = self._required_text(item.get("gh_repo"), "gh_repo")
            scope = self._required_text(item.get("scope"), "scope")
            digest = self._required_text(item.get("input_digest"), "input_digest")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("capability input_digest must be lowercase SHA-256")
            identity = (source_key, scope)
            if identity in seen:
                raise ValueError("capability manifest contains a duplicate scope")
            seen.add(identity)
            normalized.append((
                source_key,
                self._mapping_key(gh_org, gh_repo),
                gh_org,
                gh_repo,
                scope,
                digest,
            ))
        normalized.sort()
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            existing = [
                (
                    row["source_key"], row["target_key"], row["gh_org"],
                    row["gh_repo"], row["scope"], row["input_digest"],
                )
                for row in conn.execute(
                    "SELECT * FROM pev_plan_capabilities WHERE plan_id=? "
                    "ORDER BY source_key,scope",
                    (plan_id,),
                ).fetchall()
            ]
            existing.sort()
            if existing:
                if existing != normalized:
                    raise PermissionError(
                        "approved PEV plan capability manifest is immutable"
                    )
                return
            conn.executemany(
                "INSERT INTO pev_plan_capabilities "
                "(plan_id,source_key,target_key,gh_org,gh_repo,scope," 
                "input_digest,created_at) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (plan_id, *row, now)
                    for row in normalized
                ],
            )

    def get_pev_plan_capabilities(self, plan_id: str) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM pev_plan_capabilities WHERE plan_id=? "
                    "ORDER BY source_key,scope",
                    (plan_id,),
                ).fetchall()
            ]

    @classmethod
    def _canonical_destructive_request(cls, request: dict) -> tuple[str, str]:
        if not isinstance(request, dict) or not request:
            raise ValueError("destructive request must be a non-empty object")
        redacted = cls._redact_sensitive_payload(request)
        if redacted != request:
            raise ValueError(
                "destructive request must not contain credential-like values"
            )
        payload = cls._json_dumps(request)
        return payload, cls._digest(payload)

    def claim_pev_governance_approval(
        self,
        approval_evidence: dict,
        *,
        action: str,
        resource_digest: str,
        plan_id: str = "",
        run_id: str = "",
    ) -> None:
        """Atomically consume one externally verified approval nonce.

        Cryptographic verification belongs to ``SignedApprovalVerifier``;
        this persistence boundary prevents an otherwise valid short-lived
        envelope from being replayed by another process or after a restart.
        """
        action = self._required_text(action, "action")
        resource_digest = self._required_text(
            resource_digest, "resource_digest"
        )
        if not isinstance(approval_evidence, dict) or (
            approval_evidence.get("schema")
            != "ado2gh.verified-signed-approval/v1"
        ):
            raise ValueError("verified signed approval evidence is required")
        claims = approval_evidence.get("claims")
        if not isinstance(claims, dict):
            raise ValueError("verified signed approval claims are required")
        nonce = self._required_text(str(claims.get("nonce", "")), "nonce")
        subject = self._required_text(
            str(claims.get("subject", "")), "subject"
        )
        key_id = self._required_text(
            str(approval_evidence.get("key_id", "")), "key_id"
        )
        ticket = self._required_text(str(claims.get("ticket", "")), "ticket")
        plan_id = str(plan_id or "")
        run_id = str(run_id or "")
        if (
            claims.get("action") != action
            or claims.get("resource_digest") != resource_digest
            or claims.get("plan_id") != plan_id
            or claims.get("run_id") != run_id
        ):
            raise PermissionError(
                "verified approval evidence does not match the claimed authority"
            )
        redacted = self._redact_sensitive_payload(approval_evidence)
        if redacted != approval_evidence:
            raise ValueError("governance approval evidence contains sensitive data")
        envelope_digest = self._digest(approval_evidence.get("envelope", {}))
        evidence_json = self._json_dumps(approval_evidence)
        try:
            with self.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO pev_governance_nonces "
                    "(nonce,action,resource_digest,plan_id,run_id,subject,key_id,"
                    "ticket,envelope_digest,evidence_json,claimed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        nonce, action, resource_digest, plan_id, run_id,
                        subject, key_id, ticket, envelope_digest,
                        evidence_json, self._utcnow(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PermissionError(
                "signed approval nonce has already been consumed"
            ) from exc

    def assert_pev_governance_approval(
        self,
        approval_evidence: dict,
        *,
        action: str,
        resource_digest: str,
        plan_id: str = "",
        run_id: str = "",
    ) -> None:
        """Prove a capability is backed by the exact consumed signed envelope."""
        if not isinstance(approval_evidence, dict):
            raise PermissionError("signed governance approval evidence is missing")
        claims = approval_evidence.get("claims")
        if not isinstance(claims, dict):
            raise PermissionError("signed governance approval claims are missing")
        nonce = str(claims.get("nonce", ""))
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pev_governance_nonces WHERE nonce=?", (nonce,)
            ).fetchone()
        envelope_digest = self._digest(approval_evidence.get("envelope", {}))
        if not row or (
            row["action"] != action
            or row["resource_digest"] != resource_digest
            or row["plan_id"] != str(plan_id or "")
            or row["run_id"] != str(run_id or "")
            or row["subject"] != str(claims.get("subject", ""))
            or row["key_id"] != str(approval_evidence.get("key_id", ""))
            or row["ticket"] != str(claims.get("ticket", ""))
            or not hmac.compare_digest(row["envelope_digest"], envelope_digest)
        ):
            raise PermissionError(
                "signed governance approval was not consumed for this exact authority"
            )

    def ensure_pev_governance_approval(
        self,
        approval_evidence: dict,
        *,
        action: str,
        resource_digest: str,
        plan_id: str = "",
        run_id: str = "",
    ) -> None:
        """Claim once, then permit exact concurrent use within one plan/run."""
        try:
            self.claim_pev_governance_approval(
                approval_evidence,
                action=action,
                resource_digest=resource_digest,
                plan_id=plan_id,
                run_id=run_id,
            )
        except PermissionError:
            self.assert_pev_governance_approval(
                approval_evidence,
                action=action,
                resource_digest=resource_digest,
                plan_id=plan_id,
                run_id=run_id,
            )

    def get_pev_governance_approvals(
        self,
        *,
        plan_id: str,
        action: str,
        run_id: Optional[str] = None,
    ) -> list[dict]:
        """Return durable external approval evidence for one authority."""
        sql = (
            "SELECT * FROM pev_governance_nonces WHERE plan_id=? AND action=?"
        )
        params: list[str] = [str(plan_id or ""), str(action)]
        if run_id is not None:
            sql += " AND run_id=?"
            params.append(str(run_id or ""))
        sql += " ORDER BY claimed_at,nonce"
        with self._conn() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            try:
                row["evidence"] = json.loads(row.pop("evidence_json"))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "stored governance approval evidence is corrupt"
                ) from exc
        return rows

    def authorize_pev_destructive_capability(
        self,
        plan_id: str,
        run_id: str,
        operation_kind: str,
        request: dict,
        *,
        approval: dict,
    ) -> str:
        """Authorize one exact, one-shot destructive request.

        Callers must invoke this only after the operator has confirmed the
        displayed request. Reauthorizing byte-identical content is idempotent;
        changed content always receives a different capability identity.
        """
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        operation_kind = self._required_text(
            operation_kind, "operation_kind"
        )
        request_json, request_digest = self._canonical_destructive_request(
            request
        )
        if not isinstance(approval, dict) or not approval:
            raise ValueError("destructive approval evidence is required")
        approval_json = self._json_dumps(
            self._redact_sensitive_payload(approval)
        )
        approval_digest = self._digest(approval_json)
        capability_id = (
            "destructive_"
            + self._digest(
                f"{plan_id}\x1f{run_id}\x1f{operation_kind}\x1f"
                f"{request_digest}\x1f{approval_digest}"
            )[:40]
        )
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id,status FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("destructive capability run is not bound to plan")
            allowed_statuses = (
                {
                    "completed", "executed", "failed", "needs_review",
                    "validation_failed",
                }
                if operation_kind == "github_rollback"
                else {"completed"}
            )
            if run["status"] not in allowed_statuses:
                raise PermissionError(
                    f"{operation_kind} destructive capability is not allowed "
                    f"for PEV run status {run['status']!r}"
                )
            approved = conn.execute(
                "SELECT 1 FROM pev_plan_capabilities WHERE plan_id=? LIMIT 1",
                (plan_id,),
            ).fetchone()
            if not approved:
                raise PermissionError(
                    "destructive capability requires an immutable plan manifest"
                )
            active = conn.execute(
                "SELECT capability_id FROM pev_destructive_capabilities "
                "WHERE plan_id=? AND run_id=? AND operation_kind=? "
                "AND status IN ('authorized','claimed') "
                "AND capability_id<>? LIMIT 1",
                (plan_id, run_id, operation_kind, capability_id),
            ).fetchone()
            if active:
                raise RuntimeError(
                    "a different destructive capability is already authorized "
                    "or crash-stopped for this plan/run/operation"
                )
            existing = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
            if existing:
                if (
                    existing["plan_id"] != plan_id
                    or existing["run_id"] != run_id
                    or existing["operation_kind"] != operation_kind
                    or existing["request_json"] != request_json
                    or existing["request_digest"] != request_digest
                    or existing["approval_digest"] != approval_digest
                ):
                    raise PermissionError(
                        "destructive capability identity collision or changed approval"
                    )
                return capability_id
            conn.execute(
                "INSERT INTO pev_destructive_capabilities "
                "(capability_id,plan_id,run_id,operation_kind,request_json,"
                "request_digest,approval_json,approval_digest,status,authorized_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    capability_id, plan_id, run_id, operation_kind,
                    request_json, request_digest, approval_json,
                    approval_digest, "authorized", now,
                ),
            )
        return capability_id

    def claim_pev_destructive_capability(
        self,
        capability_id: str,
        plan_id: str,
        run_id: str,
        operation_kind: str,
        request: dict,
        claimant: str,
    ) -> str:
        """Atomically consume an authorization and return its claim token."""
        capability_id = self._required_text(capability_id, "capability_id")
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        operation_kind = self._required_text(
            operation_kind, "operation_kind"
        )
        claimant = self._required_text(claimant, "claimant")
        request_json, request_digest = self._canonical_destructive_request(
            request
        )
        claim_token = f"claim_{uuid.uuid4().hex}"
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
            if not row or (
                row["plan_id"] != plan_id
                or row["run_id"] != run_id
                or row["operation_kind"] != operation_kind
                or row["request_json"] != request_json
                or not hmac.compare_digest(row["request_digest"], request_digest)
            ):
                raise PermissionError(
                    "destructive capability does not authorize this exact request"
                )
            if row["status"] != "authorized":
                raise RuntimeError(
                    "destructive capability is already claimed or finished; "
                    "reconcile it before issuing a new authorization"
                )
            cur = conn.execute(
                "UPDATE pev_destructive_capabilities SET status='claimed',"
                "claimant=?,claim_token=?,claimed_at=? "
                "WHERE capability_id=? AND status='authorized'",
                (claimant, claim_token, now, capability_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("destructive capability claim was lost")
        return claim_token

    @staticmethod
    def _assert_destructive_claim_row(
        row, *, plan_id: str, run_id: str, operation_kind: str,
        claimant: str, claim_token: str,
    ) -> None:
        if not row or (
            row["plan_id"] != plan_id
            or row["run_id"] != run_id
            or row["operation_kind"] != operation_kind
            or row["status"] != "claimed"
            or row["claimant"] != claimant
            or row["claim_token"] != claim_token
        ):
            raise PermissionError("destructive capability claim is not active")

    def assert_pev_destructive_capability(
        self,
        capability_id: str,
        *,
        plan_id: str,
        run_id: str,
        operation_kind: str,
        claimant: str,
        claim_token: str,
        request: dict = None,
    ) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
        self._assert_destructive_claim_row(
            row,
            plan_id=plan_id,
            run_id=run_id,
            operation_kind=operation_kind,
            claimant=claimant,
            claim_token=claim_token,
        )
        if request is not None:
            request_json, request_digest = self._canonical_destructive_request(
                request
            )
            if (
                row["request_json"] != request_json
                or not hmac.compare_digest(row["request_digest"], request_digest)
            ):
                raise PermissionError(
                    "destructive capability request content changed after claim"
                )

    def finish_pev_destructive_capability(
        self,
        capability_id: str,
        *,
        plan_id: str,
        run_id: str,
        operation_kind: str,
        claimant: str,
        claim_token: str,
        status: str,
        error: str = None,
    ) -> bool:
        if status not in {"completed", "failed"}:
            raise ValueError("destructive capability status must be completed or failed")
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
            self._assert_destructive_claim_row(
                row,
                plan_id=plan_id,
                run_id=run_id,
                operation_kind=operation_kind,
                claimant=claimant,
                claim_token=claim_token,
            )
            in_flight = conn.execute(
                "SELECT 1 FROM pev_destructive_action_receipts "
                "WHERE capability_id=? AND status='in_progress' LIMIT 1",
                (capability_id,),
            ).fetchone()
            if in_flight:
                raise RuntimeError(
                    "cannot finish a destructive capability with an in-flight action"
                )
            cur = conn.execute(
                "UPDATE pev_destructive_capabilities SET status=?,finished_at=?,"
                "error=? WHERE capability_id=? AND status='claimed' "
                "AND claimant=? AND claim_token=?",
                (
                    status, now, str(error or "")[:2000] or None,
                    capability_id, claimant, claim_token,
                ),
            )
            return cur.rowcount == 1

    def begin_pev_destructive_action(
        self,
        capability_id: str,
        *,
        plan_id: str,
        run_id: str,
        operation_kind: str,
        claimant: str,
        claim_token: str,
        action_key: str,
        source_key: str,
        target_key: str,
        action_kind: str,
        before: dict,
    ) -> str:
        action_key = self._required_text(action_key, "action_key")
        source_key = self._required_text(source_key, "source_key")
        target_key = self._required_text(target_key, "target_key")
        action_kind = self._required_text(action_kind, "action_kind")
        if not isinstance(before, dict):
            raise ValueError("destructive action before receipt must be an object")
        before_json = self._json_dumps(
            self._redact_sensitive_payload(before)
        )
        before_digest = self._digest(before_json)
        receipt_id = (
            "destructive_action_"
            + self._digest(f"{capability_id}\x1f{action_key}")[:40]
        )
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
            self._assert_destructive_claim_row(
                row,
                plan_id=plan_id,
                run_id=run_id,
                operation_kind=operation_kind,
                claimant=claimant,
                claim_token=claim_token,
            )
            existing = conn.execute(
                "SELECT * FROM pev_destructive_action_receipts "
                "WHERE capability_id=? AND action_key=?",
                (capability_id, action_key),
            ).fetchone()
            if existing:
                if (
                    existing["source_key"] != source_key
                    or existing["target_key"] != target_key
                    or existing["action_kind"] != action_kind
                    or existing["before_digest"] != before_digest
                ):
                    raise PermissionError(
                        "destructive action receipt content changed"
                    )
                raise RuntimeError(
                    "destructive action was already dispatched; reconcile its "
                    "receipt before authorizing another attempt"
                )
            conn.execute(
                "INSERT INTO pev_destructive_action_receipts "
                "(receipt_id,capability_id,plan_id,run_id,operation_kind,"
                "action_key,source_key,target_key,action_kind,before_json,"
                "before_digest,status,started_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id, capability_id, plan_id, run_id,
                    operation_kind, action_key, source_key, target_key,
                    action_kind, before_json, before_digest, "in_progress", now,
                ),
            )
        return receipt_id

    def finish_pev_destructive_action(
        self,
        receipt_id: str,
        *,
        capability_id: str,
        claimant: str,
        claim_token: str,
        status: str,
        after: dict = None,
        error: str = None,
    ) -> bool:
        if status not in {"completed", "failed", "skipped"}:
            raise ValueError(
                "destructive action status must be completed, failed, or skipped"
            )
        after_json = self._json_dumps(
            self._redact_sensitive_payload(after or {})
        )
        after_digest = self._digest(after_json)
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            capability = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
            if not capability or (
                capability["status"] != "claimed"
                or capability["claimant"] != claimant
                or capability["claim_token"] != claim_token
            ):
                raise PermissionError("destructive capability claim is not active")
            cur = conn.execute(
                "UPDATE pev_destructive_action_receipts SET status=?,"
                "after_json=?,after_digest=?,completed_at=?,error=? "
                "WHERE receipt_id=? AND capability_id=? AND status='in_progress'",
                (
                    status, after_json, after_digest, now,
                    str(error or "")[:2000] or None,
                    receipt_id, capability_id,
                ),
            )
            return cur.rowcount == 1

    def get_pev_destructive_capability(self, capability_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pev_destructive_capabilities "
                "WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_pev_destructive_actions(self, capability_id: str) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM pev_destructive_action_receipts "
                    "WHERE capability_id=? ORDER BY started_at,receipt_id",
                    (capability_id,),
                ).fetchall()
            ]

    def acquire_pev_plan_lease(
        self,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        *,
        ttl_seconds: int = 300,
    ) -> bool:
        """Atomically acquire the cross-process executor lease for a plan."""
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        lease_owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) \
                or ttl_seconds < 30 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be between 30 and 86400")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        now_text = now.isoformat()
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("lease run is not bound to plan")
            conn.execute(
                "INSERT INTO pev_plan_leases "
                "(plan_id,run_id,lease_owner,lease_expires_at,updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET "
                "run_id=excluded.run_id,lease_owner=excluded.lease_owner,"
                "lease_expires_at=excluded.lease_expires_at,"
                "updated_at=excluded.updated_at "
                "WHERE pev_plan_leases.lease_expires_at<=excluded.updated_at "
                "OR (pev_plan_leases.run_id=excluded.run_id "
                "AND pev_plan_leases.lease_owner=excluded.lease_owner)",
                (plan_id, run_id, lease_owner, expires, now_text),
            )
            row = conn.execute(
                "SELECT run_id,lease_owner FROM pev_plan_leases WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            return bool(
                row and row["run_id"] == run_id
                and row["lease_owner"] == lease_owner
            )

    def renew_pev_plan_lease(
        self,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        *,
        ttl_seconds: int = 300,
    ) -> bool:
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        lease_owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) \
                or ttl_seconds < 30 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be between 30 and 86400")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE pev_plan_leases SET lease_expires_at=?,updated_at=? "
                "WHERE plan_id=? AND run_id=? AND lease_owner=? "
                "AND lease_expires_at>?",
                (
                    expires, now.isoformat(), plan_id, run_id, lease_owner,
                    now.isoformat(),
                ),
            )
            return cur.rowcount == 1

    def release_pev_plan_lease(
        self, plan_id: str, run_id: str, lease_owner: str
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM pev_plan_leases "
                "WHERE plan_id=? AND run_id=? AND lease_owner=?",
                (plan_id, run_id, lease_owner),
            )
            return cur.rowcount == 1

    def acquire_pev_target_leases(
        self,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        targets: list[tuple[str, str]],
        *,
        ttl_seconds: int = 300,
    ) -> Optional[dict[str, int]]:
        """Atomically fence every target used by an execution or validator."""
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        lease_owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) \
                or ttl_seconds < 30 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be between 30 and 86400")
        normalized: dict[str, tuple[str, str]] = {}
        for gh_org, gh_repo in targets:
            gh_org = self._required_text(gh_org, "gh_org")
            gh_repo = self._required_text(gh_repo, "gh_repo")
            key = self._mapping_key(gh_org, gh_repo)
            existing = normalized.get(key)
            if existing and existing != (gh_org, gh_repo):
                raise ValueError("target list contains a canonical collision")
            normalized[key] = (gh_org, gh_repo)
        if not normalized:
            raise ValueError("at least one target lease is required")

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("target lease run is not bound to plan")
            approved_targets = {
                row["target_key"]
                for row in conn.execute(
                    "SELECT DISTINCT target_key FROM pev_plan_capabilities "
                    "WHERE plan_id=?",
                    (plan_id,),
                ).fetchall()
            }
            unapproved = set(normalized) - approved_targets
            if unapproved:
                raise PermissionError(
                    "target lease request is outside the immutable approved plan"
                )
            placeholders = ",".join("?" for _ in normalized)
            unresolved = conn.execute(
                "SELECT operation_id,target_key FROM pev_remote_operations "
                "WHERE status='in_flight' AND target_key IN (" 
                + placeholders + ") LIMIT 1",
                tuple(normalized),
            ).fetchone()
            if unresolved:
                return None
            rows = {
                row["target_key"]: row
                for row in conn.execute(
                    "SELECT * FROM pev_target_leases WHERE target_key IN ("
                    + placeholders
                    + ")",
                    tuple(normalized),
                ).fetchall()
            }
            for key, row in rows.items():
                same_owner = (
                    row["plan_id"] == plan_id
                    and row["run_id"] == run_id
                    and row["lease_owner"] == lease_owner
                )
                if row["lease_expires_at"] > now and not same_owner:
                    return None

            tokens: dict[str, int] = {}
            for key, (gh_org, gh_repo) in normalized.items():
                row = rows.get(key)
                same_owner = bool(
                    row
                    and row["plan_id"] == plan_id
                    and row["run_id"] == run_id
                    and row["lease_owner"] == lease_owner
                )
                token = int(row["fencing_token"]) if same_owner else (
                    int(row["fencing_token"]) + 1 if row else 1
                )
                conn.execute(
                    "INSERT INTO pev_target_leases "
                    "(target_key,gh_org,gh_repo,plan_id,run_id,lease_owner,"
                    "fencing_token,lease_expires_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(target_key) "
                    "DO UPDATE SET gh_org=excluded.gh_org,gh_repo=excluded.gh_repo,"
                    "plan_id=excluded.plan_id,run_id=excluded.run_id,"
                    "lease_owner=excluded.lease_owner,"
                    "fencing_token=excluded.fencing_token,"
                    "lease_expires_at=excluded.lease_expires_at,"
                    "updated_at=excluded.updated_at",
                    (
                        key, gh_org, gh_repo, plan_id, run_id, lease_owner,
                        token, expires, now,
                    ),
                )
                tokens[key] = token
            return tokens

    def renew_pev_target_leases(
        self,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        fencing_tokens: dict[str, int],
        *,
        ttl_seconds: int = 300,
    ) -> bool:
        if not fencing_tokens:
            return False
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        with self.transaction(immediate=True) as conn:
            for key, token in fencing_tokens.items():
                row = conn.execute(
                    "SELECT plan_id,run_id,lease_owner,fencing_token,"
                    "lease_expires_at FROM pev_target_leases WHERE target_key=?",
                    (key,),
                ).fetchone()
                if not row or (
                    row["plan_id"] != plan_id
                    or row["run_id"] != run_id
                    or row["lease_owner"] != lease_owner
                    or int(row["fencing_token"]) != int(token)
                    or row["lease_expires_at"] <= now
                ):
                    return False
            for key in fencing_tokens:
                conn.execute(
                    "UPDATE pev_target_leases SET lease_expires_at="
                    "CASE WHEN lease_expires_at>? THEN lease_expires_at ELSE ? END,"
                    "updated_at=? "
                    "WHERE target_key=?",
                    (expires, expires, now, key),
                )
            return True

    def assert_pev_target_lease(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        fencing_token: int,
    ) -> None:
        key = self._mapping_key(gh_org, gh_repo)
        now = self._utcnow()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT plan_id,run_id,lease_owner,fencing_token,"
                "lease_expires_at FROM pev_target_leases WHERE target_key=?",
                (key,),
            ).fetchone()
        if not row or (
            row["plan_id"] != plan_id
            or row["run_id"] != run_id
            or row["lease_owner"] != lease_owner
            or int(row["fencing_token"]) != int(fencing_token)
            or row["lease_expires_at"] <= now
        ):
            raise RuntimeError(
                f"PEV target lease was lost for {gh_org}/{gh_repo}; write blocked"
            )

    def begin_pev_remote_operation(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        fencing_token: int,
        operation_kind: str,
        operation_payload: dict = None,
    ) -> str:
        """Persist an indefinite crash-stop barrier before a remote write."""
        gh_org = self._required_text(gh_org, "gh_org")
        gh_repo = self._required_text(gh_repo, "gh_repo")
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        lease_owner = self._required_text(lease_owner, "lease_owner")
        operation_kind = self._required_text(
            operation_kind, "operation_kind"
        )
        if (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token < 1
        ):
            raise ValueError("fencing_token must be a positive integer")
        target_key = self._mapping_key(gh_org, gh_repo)
        now = self._utcnow()
        operation_id = f"remote_op_{uuid.uuid4().hex}"
        payload = operation_payload or {}
        if not isinstance(payload, dict):
            raise ValueError("operation_payload must be an object")
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("remote operation run is not bound to plan")
            lease = conn.execute(
                "SELECT plan_id,run_id,lease_owner,fencing_token," 
                "lease_expires_at FROM pev_target_leases WHERE target_key=?",
                (target_key,),
            ).fetchone()
            if not lease or (
                lease["plan_id"] != plan_id
                or lease["run_id"] != run_id
                or lease["lease_owner"] != lease_owner
                or int(lease["fencing_token"]) != fencing_token
                or lease["lease_expires_at"] <= now
            ):
                raise RuntimeError("target lease was lost before remote dispatch")
            active = conn.execute(
                "SELECT operation_id FROM pev_remote_operations "
                "WHERE target_key=? AND status='in_flight'",
                (target_key,),
            ).fetchone()
            if active:
                raise RuntimeError(
                    "target has an unresolved remote operation; manual "
                    "reconciliation is required"
                )
            conn.execute(
                "INSERT INTO pev_remote_operations "
                "(operation_id,target_key,gh_org,gh_repo,plan_id,run_id," 
                "lease_owner,fencing_token,operation_kind,operation_digest," 
                "status,started_at) VALUES (?,?,?,?,?,?,?,?,?,?,'in_flight',?)",
                (
                    operation_id, target_key, gh_org, gh_repo, plan_id, run_id,
                    lease_owner, fencing_token, operation_kind,
                    self._digest(payload), now,
                ),
            )
        return operation_id

    def finish_pev_remote_operation(
        self,
        operation_id: str,
        *,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        fencing_token: int,
        resolution: dict = None,
    ) -> bool:
        """Resolve a barrier only after the caller observed terminal success."""
        operation_id = self._required_text(operation_id, "operation_id")
        resolution = resolution or {}
        if not isinstance(resolution, dict):
            raise ValueError("resolution must be an object")
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE pev_remote_operations SET status='succeeded'," 
                "resolution_digest=?,resolved_at=? WHERE operation_id=? "
                "AND status='in_flight' AND plan_id=? AND run_id=? "
                "AND lease_owner=? AND fencing_token=?",
                (
                    self._digest(resolution), now, operation_id, plan_id,
                    run_id, lease_owner, int(fencing_token),
                ),
            )
            return cur.rowcount == 1

    def list_pev_remote_operations(
        self, gh_org: str = None, gh_repo: str = None
    ) -> list[dict]:
        sql = "SELECT * FROM pev_remote_operations"
        params: tuple = ()
        if gh_org is not None or gh_repo is not None:
            if gh_org is None or gh_repo is None:
                raise ValueError("gh_org and gh_repo must be supplied together")
            sql += " WHERE target_key=?"
            params = (self._mapping_key(gh_org, gh_repo),)
        sql += " ORDER BY started_at,operation_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def release_pev_target_leases(
        self,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        fencing_tokens: dict[str, int],
    ) -> int:
        released = 0
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            for key, token in fencing_tokens.items():
                cur = conn.execute(
                    "UPDATE pev_target_leases SET lease_expires_at=?,updated_at=? "
                    "WHERE target_key=? "
                    "AND plan_id=? AND run_id=? AND lease_owner=? "
                    "AND fencing_token=?",
                    (
                        now, now, key, plan_id, run_id, lease_owner, int(token)
                    ),
                )
                released += cur.rowcount
        return released

    def quarantine_pev_target_lease(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        plan_id: str,
        run_id: str,
        lease_owner: str,
        fencing_token: int,
        ttl_seconds: int,
    ) -> bool:
        """Keep an uncertain in-flight target write fenced after worker exit.

        Changing the owner prevents the executor's normal ``finally`` release
        from shortening the quarantine. A later owner can acquire the target
        only after the conservative deadline expires.
        """
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or (ttl_seconds != 0 and not 30 <= ttl_seconds <= 86_400)
        ):
            raise ValueError(
                "ttl_seconds must be 0 (manual reconciliation) or between "
                "30 and 86400"
            )
        key = self._mapping_key(gh_org, gh_repo)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (
            "9999-12-31T23:59:59.999999+00:00"
            if ttl_seconds == 0
            else (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        )
        quarantine_owner = f"quarantine:{lease_owner}"
        with self.transaction(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE pev_target_leases SET lease_owner=?,lease_expires_at="
                "CASE WHEN lease_expires_at>? THEN lease_expires_at ELSE ? END,"
                "updated_at=? WHERE target_key=? AND plan_id=? AND run_id=? "
                "AND lease_owner=? AND fencing_token=?",
                (
                    quarantine_owner,
                    expires,
                    expires,
                    now,
                    key,
                    plan_id,
                    run_id,
                    lease_owner,
                    int(fencing_token),
                ),
            )
            return cur.rowcount == 1

    def release_pev_target_quarantine(
        self,
        gh_org: str,
        gh_repo: str,
        *,
        plan_id: str,
        run_id: str,
        approval_ticket: str,
        observed_target_repo_id: str = "",
    ) -> dict:
        """Release a quarantined target after explicit external reconciliation."""
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        approval_ticket = self._required_text(approval_ticket, "approval_ticket")
        key = self._mapping_key(gh_org, gh_repo)
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM pev_target_leases WHERE target_key=?",
                (key,),
            ).fetchone()
            if not row or row["plan_id"] != plan_id or row["run_id"] != run_id:
                raise PermissionError(
                    "Target quarantine is not owned by the approved plan and run"
                )
            if not str(row["lease_owner"]).startswith("quarantine:"):
                raise RuntimeError("Target does not have a quarantined lease")
            evidence = {
                "target": f"{gh_org}/{gh_repo}",
                "plan_id": plan_id,
                "run_id": run_id,
                "fencing_token": int(row["fencing_token"]),
                "quarantine_expires_at": row["lease_expires_at"],
                "approval_ticket": approval_ticket,
                "observed_target_repo_id": str(observed_target_repo_id or ""),
                "released_at": now,
            }
            evidence_json = self._json_dumps(evidence)
            evidence_digest = self._digest(evidence_json)
            evidence_id = f"evidence_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO validation_evidence "
                "(evidence_id,run_id,task_id,category,verdict,evidence_json,"
                "evidence_digest,created_at) VALUES (?,?,NULL,?,?,?,?,?)",
                (
                    evidence_id,
                    run_id,
                    "target_quarantine_release",
                    "pass",
                    evidence_json,
                    evidence_digest,
                    now,
                ),
            )
            conn.execute(
                "UPDATE pev_remote_operations SET status='reconciled',"
                "resolution_digest=?,resolved_at=? WHERE target_key=? "
                "AND plan_id=? AND run_id=? AND status='in_flight'",
                (evidence_digest, now, key, plan_id, run_id),
            )
            cur = conn.execute(
                "UPDATE pev_target_leases SET lease_owner=?,lease_expires_at=?,"
                "updated_at=? WHERE target_key=? AND plan_id=? AND run_id=? "
                "AND fencing_token=? AND lease_owner=?",
                (
                    f"released:{self._digest(approval_ticket)[:16]}",
                    now,
                    now,
                    key,
                    plan_id,
                    run_id,
                    int(row["fencing_token"]),
                    row["lease_owner"],
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Target quarantine changed during release")
        return evidence

    def renew_pev_task_leases(
        self, lease_owner: str, *, ttl_seconds: int = 300
    ) -> int:
        """Renew all active task claims held by one executor heartbeat."""
        lease_owner = self._required_text(lease_owner, "lease_owner")
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE pev_tasks SET lease_expires_at=?,updated_at=? "
                "WHERE lease_owner=? AND status='in_progress' "
                "AND lease_expires_at>?",
                (expires, now_dt.isoformat(), lease_owner, now_dt.isoformat()),
            )
            return cur.rowcount

    def create_pev_run(
        self,
        plan_id: str,
        config_digest: str = "",
        status: str = "planned",
        summary: dict = None,
        run_id: str = None,
    ) -> str:
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        return self.upsert_pev_run(
            run_id, plan_id, status=status, config_digest=config_digest,
            summary=summary,
        )

    def upsert_pev_run(
        self,
        run_id: str,
        plan_id: str,
        status: str = "planned",
        config_digest: str = "",
        summary: dict = None,
    ) -> str:
        run_id = self._required_text(run_id, "run_id")
        plan_id = self._required_text(plan_id, "plan_id")
        status = self._required_text(status, "status")
        if config_digest is None:
            config_digest = ""
        if not isinstance(config_digest, str):
            raise ValueError("config_digest must be a string")
        now = self._utcnow()
        started = now if status in {"in_progress", "executing", "validating"} else None
        completed = now if status in {
            "completed", "failed", "cancelled", "blocked", "validated",
            "validation_failed", "needs_review", "dry_run", "dry_run_passed",
            "dry_run_failed", "dry_run_needs_review",
        } else None
        with self.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT plan_id,config_digest,summary_json,status "
                "FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                if existing["plan_id"] != plan_id:
                    raise ValueError(
                        f"run_id {run_id!r} is already bound to plan "
                        f"{existing['plan_id']!r}"
                    )
                if config_digest and existing["config_digest"] \
                        and config_digest != existing["config_digest"]:
                    raise ValueError(
                        f"run_id {run_id!r} is already bound to a different "
                        "configuration digest"
                    )
                config_digest = config_digest or existing["config_digest"]
                summary_json = (
                    existing["summary_json"] if summary is None
                    else self._json_dumps(summary)
                )
            else:
                summary_json = self._json_dumps(summary or {})
            conn.execute(
                """
                INSERT INTO pev_runs
                    (run_id,plan_id,status,config_digest,summary_json,created_at,
                     started_at,completed_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=CASE
                        WHEN pev_runs.status IN
                             ('completed','cancelled','validated')
                             AND excluded.status='planned'
                        THEN pev_runs.status
                        ELSE excluded.status
                    END,
                    config_digest=excluded.config_digest,
                    summary_json=excluded.summary_json,
                    started_at=COALESCE(pev_runs.started_at,excluded.started_at),
                    completed_at=CASE
                        WHEN pev_runs.status IN
                             ('completed','cancelled','validated')
                             AND excluded.status='planned'
                        THEN pev_runs.completed_at
                        WHEN pev_runs.status=excluded.status
                        THEN COALESCE(pev_runs.completed_at,excluded.completed_at)
                        ELSE excluded.completed_at
                    END,
                    updated_at=excluded.updated_at
                """,
                (run_id, plan_id, status, config_digest, summary_json, now,
                 started, completed, now),
            )
        return run_id

    def get_pev_run(self, run_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_pev_runs(self, status: str = None, limit: int = None) -> list[dict]:
        params: list = []
        sql = "SELECT * FROM pev_runs"
        if status is not None:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC,run_id"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise ValueError("limit must be a positive integer")
            sql += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def upsert_pev_task(
        self,
        run_id: str,
        kind: str,
        input_digest: str,
        source_ref: str = "",
        target_ref: str = "",
        status: str = "pending",
        task_id: str = None,
        strategy: str = "deterministic",
        dependencies: list[str] = None,
        idempotency_key: str = None,
        max_attempts: int = 3,
        result: dict = None,
        error: str = None,
        not_before: str = None,
    ) -> str:
        run_id = self._required_text(run_id, "run_id")
        kind = self._required_text(kind, "kind")
        input_digest = self._required_text(input_digest, "input_digest")
        status = self._required_text(status, "status")
        strategy = self._required_text(strategy, "strategy")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) \
                or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        source_ref = str(source_ref or "")
        target_ref = str(target_ref or "")
        if dependencies is not None and not isinstance(dependencies, (list, tuple)):
            raise ValueError("dependencies must be a list or tuple of task IDs")
        dependencies = list(dependencies or [])
        if any(not isinstance(dep, str) or not dep.strip() for dep in dependencies):
            raise ValueError("dependencies must contain non-empty task IDs")
        identity = {
            "run_id": run_id,
            "kind": kind,
            "source_ref": source_ref,
            "target_ref": target_ref,
            "input_digest": input_digest,
        }
        idempotency_key = idempotency_key or self._digest(identity)
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        task_id = task_id or f"task_{self._digest(run_id + ':' + idempotency_key)[:32]}"
        task_id = self._required_text(task_id, "task_id")
        now = self._utcnow()
        completed = now if status in {
            "completed", "failed", "cancelled", "blocked", "skipped",
            "needs_review",
        } else None
        with self.transaction(immediate=True) as conn:
            collision = conn.execute(
                "SELECT task_id,input_digest FROM pev_tasks "
                "WHERE run_id=? AND idempotency_key=?",
                (run_id, idempotency_key),
            ).fetchone()
            if collision and collision["task_id"] != task_id:
                raise ValueError(
                    f"idempotency_key already belongs to task {collision['task_id']}"
                )
            existing = conn.execute(
                "SELECT run_id,input_digest,idempotency_key FROM pev_tasks "
                "WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing and (
                existing["run_id"] != run_id
                or existing["input_digest"] != input_digest
                or existing["idempotency_key"] != idempotency_key
            ):
                raise ValueError(
                    "task_id is already associated with different immutable input"
                )
            conn.execute(
                """
                INSERT INTO pev_tasks
                    (task_id,run_id,kind,source_ref,target_ref,input_digest,
                     idempotency_key,strategy,dependency_ids,status,attempt,
                     max_attempts,not_before,result_json,error,created_at,
                     completed_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=CASE
                        WHEN pev_tasks.status IN
                             ('completed','cancelled','skipped')
                             AND excluded.status='pending'
                        THEN pev_tasks.status
                        ELSE excluded.status
                    END,
                    strategy=excluded.strategy,
                    dependency_ids=excluded.dependency_ids,
                    max_attempts=excluded.max_attempts,
                    not_before=excluded.not_before,
                    result_json=CASE
                        WHEN pev_tasks.status IN
                             ('completed','cancelled','skipped')
                             AND excluded.status='pending'
                        THEN pev_tasks.result_json
                        ELSE excluded.result_json
                    END,
                    error=CASE
                        WHEN pev_tasks.status IN
                             ('completed','cancelled','skipped')
                             AND excluded.status='pending'
                        THEN pev_tasks.error
                        ELSE excluded.error
                    END,
                    completed_at=CASE
                        WHEN pev_tasks.status IN
                             ('completed','cancelled','skipped')
                             AND excluded.status='pending'
                        THEN pev_tasks.completed_at
                        WHEN pev_tasks.status=excluded.status
                        THEN COALESCE(pev_tasks.completed_at,excluded.completed_at)
                        ELSE excluded.completed_at
                    END,
                    updated_at=excluded.updated_at
                """,
                (task_id, run_id, kind, source_ref, target_ref, input_digest,
                 idempotency_key, strategy, self._json_dumps(dependencies), status,
                 max_attempts, not_before, self._json_dumps(result or {}), error,
                 now, completed, now),
            )
        return task_id

    def get_pev_task(self, task_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pev_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_pev_tasks(self, run_id: str, status: str = None,
                       kind: str = None) -> list[dict]:
        sql = "SELECT * FROM pev_tasks WHERE run_id=?"
        params: list = [run_id]
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY created_at,task_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def claim_pev_task(self, task_id: str, lease_owner: str,
                       lease_seconds: int = 300) -> Optional[dict]:
        """Atomically lease a runnable task and increment its attempt count."""
        task_id = self._required_text(task_id, "task_id")
        lease_owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) \
                or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self.transaction(immediate=True) as conn:
            cur = conn.execute(
                """
                UPDATE pev_tasks SET
                    status='in_progress',
                    attempt=attempt+1,
                    lease_owner=?,
                    lease_expires_at=?,
                    started_at=COALESCE(started_at,?),
                    completed_at=NULL,
                    error=NULL,
                    updated_at=?
                WHERE task_id=?
                  AND (
                      status IN ('pending','retry')
                      OR (status='in_progress' AND lease_expires_at<=?)
                  )
                  AND attempt < max_attempts
                  AND (not_before IS NULL OR not_before<=?)
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                """,
                (lease_owner, expires, now, now, task_id, now, now, now),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM pev_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            return dict(row)

    def update_pev_task(
        self,
        task_id: str,
        status: str,
        result: dict = None,
        error: str = None,
        lease_owner: str = None,
        expected_status: str = None,
    ) -> bool:
        """Update task state, optionally using optimistic/lease ownership checks."""
        status = self._required_text(status, "status")
        now = self._utcnow()
        terminal = status in {
            "completed", "failed", "cancelled", "blocked", "skipped",
            "needs_review",
        }
        assignments = [
            "status=?", "result_json=?", "error=?", "updated_at=?",
            "completed_at=?",
        ]
        params: list = [
            status, self._json_dumps(result or {}), error, now,
            now if terminal else None,
        ]
        if terminal or status in {"pending", "retry"}:
            assignments.extend(["lease_owner=NULL", "lease_expires_at=NULL"])
        where = ["task_id=?"]
        params.append(task_id)
        if lease_owner is not None:
            where.append("lease_owner=?")
            params.append(lease_owner)
        if expected_status is not None:
            where.append("status=?")
            params.append(expected_status)
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE pev_tasks SET {','.join(assignments)} "
                f"WHERE {' AND '.join(where)}", params,
            )
            return cur.rowcount == 1

    def record_validation_evidence(
        self,
        run_id: str,
        category: str,
        verdict: str,
        evidence: dict = None,
        task_id: str = None,
        evidence_id: str = None,
    ) -> str:
        run_id = self._required_text(run_id, "run_id")
        category = self._required_text(category, "category")
        verdict = self._required_text(verdict, "verdict")
        evidence_json = self._json_dumps(
            self._redact_sensitive_payload(evidence or {})
        )
        evidence_digest = self._digest(evidence_json)
        identity = f"{run_id}\x1f{task_id or ''}\x1f{category}\x1f{evidence_digest}"
        evidence_id = evidence_id or f"evidence_{self._digest(identity)[:32]}"
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            if task_id is not None:
                owner = conn.execute(
                    "SELECT run_id FROM pev_tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if not owner or owner["run_id"] != run_id:
                    raise ValueError("task_id does not belong to run_id")
            conn.execute(
                "INSERT INTO validation_evidence "
                "(evidence_id,run_id,task_id,category,verdict,evidence_json,"
                " evidence_digest,created_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(evidence_id) DO NOTHING",
                (evidence_id, run_id, task_id, category, verdict,
                 evidence_json, evidence_digest, now),
            )
        return evidence_id

    def list_validation_evidence(self, run_id: str, task_id: str = None,
                                 verdict: str = None) -> list[dict]:
        sql = "SELECT * FROM validation_evidence WHERE run_id=?"
        params: list = [run_id]
        if task_id is not None:
            sql += " AND task_id=?"
            params.append(task_id)
        if verdict is not None:
            sql += " AND verdict=?"
            params.append(verdict)
        sql += " ORDER BY created_at,evidence_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def record_llm_decision(
        self,
        run_id: str,
        provider: str,
        model: str,
        prompt_digest: str,
        response_digest: str,
        reason: str = "",
        confidence: float = None,
        task_id: str = None,
        schema_valid: bool = None,
        policy_status: str = "",
        artifact_ref: str = "",
        decision_id: str = None,
    ) -> str:
        """Append redacted LLM decision metadata (never raw prompts/responses)."""
        for value, name in (
            (run_id, "run_id"), (provider, "provider"), (model, "model"),
            (prompt_digest, "prompt_digest"),
            (response_digest, "response_digest"),
        ):
            self._required_text(value, name)
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
                    or not 0 <= float(confidence) <= 1:
                raise ValueError("confidence must be between 0 and 1")
            confidence = float(confidence)
        identity = (
            f"{run_id}\x1f{task_id or ''}\x1f{prompt_digest}\x1f"
            f"{response_digest}"
        )
        decision_id = decision_id or f"decision_{self._digest(identity)[:32]}"
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            if task_id is not None:
                owner = conn.execute(
                    "SELECT run_id FROM pev_tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if not owner or owner["run_id"] != run_id:
                    raise ValueError("task_id does not belong to run_id")
            conn.execute(
                "INSERT INTO llm_decisions "
                "(decision_id,run_id,task_id,provider,model,prompt_digest,"
                " response_digest,reason,confidence,schema_valid,policy_status,"
                " artifact_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(decision_id) DO NOTHING",
                (decision_id, run_id, task_id, provider, model, prompt_digest,
                 response_digest, self._redact_sensitive_payload(str(reason or "")), confidence,
                 None if schema_valid is None else int(bool(schema_valid)),
                 str(policy_status or ""),
                 self._redact_sensitive_payload(str(artifact_ref or "")), now),
            )
        return decision_id

    def list_llm_decisions(self, run_id: str, task_id: str = None) -> list[dict]:
        sql = "SELECT * FROM llm_decisions WHERE run_id=?"
        params: list = [run_id]
        if task_id is not None:
            sql += " AND task_id=?"
            params.append(task_id)
        sql += " ORDER BY created_at,decision_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # ── Append-only pipeline conversion attempts ───────────────────────────

    def start_pipeline_conversion_attempt(
        self,
        wave_id: int,
        project: str,
        pipeline_id: int,
        source_fingerprint: str,
        *,
        pipeline_type: str = "yaml",
        plan_id: str = "",
        ruleset_version: str = "",
        mode: str = "deterministic",
        llm_used: bool = False,
        provider: str = "",
        model: str = "",
        prompt_digest: str = "",
        correlation_id: str = "",
        attempt_id: str = None,
    ) -> str:
        project = self._required_text(project, "project")
        source_fingerprint = self._required_text(
            source_fingerprint, "source_fingerprint"
        )
        pipeline_type = self._required_text(pipeline_type, "pipeline_type")
        if isinstance(wave_id, bool) or not isinstance(wave_id, int):
            raise ValueError("wave_id must be an integer")
        if isinstance(pipeline_id, bool) or not isinstance(pipeline_id, int):
            raise ValueError("pipeline_id must be an integer")
        attempt_id = attempt_id or f"pipe_attempt_{uuid.uuid4().hex}"
        now = self._utcnow()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_conversion_attempts "
                "(attempt_id,wave_id,project,pipeline_id,pipeline_type,plan_id,source_fingerprint,"
                " ruleset_version,mode,llm_used,provider,model,prompt_digest,"
                " started_at,status,correlation_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'in_progress',?)",
                (attempt_id, wave_id, project, pipeline_id, pipeline_type, plan_id,
                 source_fingerprint, ruleset_version, mode, int(bool(llm_used)),
                 provider, model, prompt_digest, now, correlation_id),
            )
        return attempt_id

    def finish_pipeline_conversion_attempt(
        self,
        attempt_id: str,
        status: str,
        *,
        response_digest: str = "",
        resolution_count: int = 0,
        validation_status: str = "pending",
        production_ready: bool = False,
        validation_report: dict = None,
        evidence_file: str = "",
        error: str = None,
    ) -> bool:
        attempt_id = self._required_text(attempt_id, "attempt_id")
        status = self._required_text(status, "status")
        if isinstance(resolution_count, bool) or not isinstance(resolution_count, int) \
                or resolution_count < 0:
            raise ValueError("resolution_count must be a non-negative integer")
        now = self._utcnow()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE pipeline_conversion_attempts SET finished_at=?,status=?,"
                "response_digest=?,resolution_count=?,validation_status=?,"
                "production_ready=?,validation_report_json=?,evidence_file=?,error=? "
                "WHERE attempt_id=? AND finished_at IS NULL",
                (now, status, response_digest, resolution_count, validation_status,
                 int(bool(production_ready)),
                 self._json_dumps(self._redact_sensitive_payload(
                     validation_report or {}
                 )),
                 self._redact_sensitive_payload(str(evidence_file or "")),
                 None if error is None else self._redact_sensitive_payload(str(error)),
                 attempt_id),
            )
            return cur.rowcount == 1

    def get_latest_pipeline_conversion_attempt(
        self, wave_id: int, project: str, pipeline_id: int,
        pipeline_type: str = None,
    ) -> Optional[dict]:
        with self._conn() as conn:
            if pipeline_type is None:
                row = conn.execute(
                    "SELECT * FROM pipeline_conversion_attempts "
                    "WHERE wave_id=? AND project=? AND pipeline_id=? "
                    "ORDER BY started_at DESC,attempt_id DESC LIMIT 1",
                    (wave_id, project, pipeline_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM pipeline_conversion_attempts "
                    "WHERE wave_id=? AND project=? AND pipeline_id=? "
                    "AND pipeline_type=? "
                    "ORDER BY started_at DESC,attempt_id DESC LIMIT 1",
                    (wave_id, project, pipeline_id, pipeline_type),
                ).fetchone()
            return dict(row) if row else None

    # ── Repo-scope migrations ───────────────────────────────────────────────

    def register_pev_scope_expectations(
        self,
        wave_id: int,
        repo: RepoConfig,
        scopes: list[str],
        *,
        plan_id: str,
        run_id: str,
        input_digests: dict[str, str],
    ) -> None:
        """Register immutable exact-run scope inputs used for safe resume."""
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        if isinstance(wave_id, bool) or not isinstance(wave_id, int):
            raise ValueError("wave_id must be an integer")
        if not scopes:
            raise ValueError("at least one expected scope is required")
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        target_key = f"{repo.gh_org}/{repo.gh_repo}"
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            run = conn.execute(
                "SELECT plan_id FROM pev_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["plan_id"] != plan_id:
                raise ValueError("scope expectation run is not bound to plan")
            for raw_scope in scopes:
                scope = self._required_text(raw_scope, "scope")
                digest = self._required_text(
                    input_digests.get(scope, ""),
                    f"input digest for {scope}",
                )
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("scope input digest must be lowercase SHA-256")
                identity = {
                    "plan_id": plan_id,
                    "run_id": run_id,
                    "source_key": source_key,
                    "target_key": target_key,
                    "scope": scope,
                    "input_digest": digest,
                }
                capability = conn.execute(
                    "SELECT target_key,input_digest FROM pev_plan_capabilities "
                    "WHERE plan_id=? AND source_key=? AND scope=?",
                    (plan_id, source_key, scope),
                ).fetchone()
                if not capability or (
                    capability["target_key"]
                    != self._mapping_key(repo.gh_org, repo.gh_repo)
                    or capability["input_digest"] != digest
                ):
                    raise PermissionError(
                        "scope expectation is outside the immutable approved plan"
                    )
                existing = conn.execute(
                    "SELECT * FROM pev_scope_expectations WHERE plan_id=? "
                    "AND run_id=? AND source_key=? AND scope=?",
                    (plan_id, run_id, source_key, scope),
                ).fetchone()
                if existing:
                    expected = (
                        target_key, digest, repo.ado_project, repo.ado_repo,
                        repo.gh_org, repo.gh_repo, wave_id,
                    )
                    actual = (
                        existing["target_key"], existing["input_digest"],
                        existing["ado_project"], existing["ado_repo"],
                        existing["gh_org"], existing["gh_repo"],
                        int(existing["wave_id"]),
                    )
                    if actual != expected:
                        raise PermissionError(
                            "PEV scope expectation is immutable and does not "
                            "match the approved task"
                        )
                    continue
                conn.execute(
                    "INSERT INTO pev_scope_expectations "
                    "(expectation_id,plan_id,run_id,wave_id,source_key," 
                    "target_key,ado_project,ado_repo,gh_org,gh_repo,scope," 
                    "input_digest,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"scope_expect_{self._digest(identity)[:32]}",
                        plan_id, run_id, wave_id, source_key, target_key,
                        repo.ado_project, repo.ado_repo, repo.gh_org,
                        repo.gh_repo, scope, digest, now,
                    ),
                )

    def upsert_pev_scope_receipt(
        self,
        wave_id: int,
        repo: RepoConfig,
        scope: str,
        status,
        *,
        plan_id: str,
        run_id: str,
        input_digest: str,
        stats: dict = None,
        error: str = None,
    ) -> None:
        """Write one scope result only against its exact approved expectation."""
        plan_id = self._required_text(plan_id, "plan_id")
        run_id = self._required_text(run_id, "run_id")
        scope = self._required_text(scope, "scope")
        input_digest = self._required_text(input_digest, "input_digest")
        status_value = status.value if isinstance(status, MigrationStatus) else str(status)
        status_value = self._required_text(status_value, "status")
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        target_key = f"{repo.gh_org}/{repo.gh_repo}"
        now = self._utcnow()
        terminal = status_value in {
            MigrationStatus.COMPLETED.value,
            MigrationStatus.FAILED.value,
            MigrationStatus.ROLLED_BACK.value,
            MigrationStatus.NEEDS_REVIEW.value,
        }
        with self.transaction(immediate=True) as conn:
            expectation = conn.execute(
                "SELECT * FROM pev_scope_expectations WHERE plan_id=? AND "
                "run_id=? AND source_key=? AND scope=?",
                (plan_id, run_id, source_key, scope),
            ).fetchone()
            if not expectation or (
                expectation["target_key"] != target_key
                or expectation["input_digest"] != input_digest
                or int(expectation["wave_id"]) != int(wave_id)
            ):
                raise PermissionError(
                    "scope receipt does not match its immutable PEV expectation"
                )
            existing = conn.execute(
                "SELECT receipt_id,input_digest,status FROM pev_scope_receipts "
                "WHERE plan_id=? AND run_id=? AND source_key=? AND scope=?",
                (plan_id, run_id, source_key, scope),
            ).fetchone()
            if existing and existing["input_digest"] != input_digest:
                raise PermissionError("scope receipt input digest cannot be rebound")
            if existing and existing["status"] in {
                MigrationStatus.COMPLETED.value,
                MigrationStatus.ROLLED_BACK.value,
            } and status_value == MigrationStatus.IN_PROGRESS.value:
                raise PermissionError(
                    "terminal scope receipt cannot be reopened without a new run"
                )
            receipt_id = (
                str(existing["receipt_id"])
                if existing else
                f"scope_receipt_{self._digest({**dict(expectation), 'kind': 'receipt'})[:32]}"
            )
            conn.execute(
                "INSERT INTO pev_scope_receipts "
                "(receipt_id,plan_id,run_id,wave_id,source_key,target_key," 
                "ado_project,ado_repo,gh_org,gh_repo,scope,input_digest,status," 
                "stats_json,error,started_at,completed_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(plan_id,run_id,source_key,scope) DO UPDATE SET "
                "status=excluded.status,stats_json=excluded.stats_json," 
                "error=excluded.error,started_at=COALESCE(" 
                "pev_scope_receipts.started_at,excluded.started_at)," 
                "completed_at=excluded.completed_at,updated_at=excluded.updated_at",
                (
                    receipt_id, plan_id, run_id, wave_id, source_key, target_key,
                    repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
                    scope, input_digest, status_value,
                    self._json_dumps(stats or {}), error,
                    now if status_value == MigrationStatus.IN_PROGRESS.value else None,
                    now if terminal else None, now,
                ),
            )

    def get_pev_scope_receipts(
        self,
        plan_id: str,
        run_id: str,
        *,
        source_key: str = None,
    ) -> list[dict]:
        sql = "SELECT * FROM pev_scope_receipts WHERE plan_id=? AND run_id=?"
        params: list = [plan_id, run_id]
        if source_key is not None:
            sql += " AND source_key=?"
            params.append(source_key)
        sql += " ORDER BY source_key,scope,receipt_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def register_migration_expectations(
        self, wave_id: int, repo: RepoConfig, scopes: list[str]
    ) -> None:
        if isinstance(wave_id, bool) or not isinstance(wave_id, int):
            raise ValueError("wave_id must be an integer")
        if not scopes:
            raise ValueError("at least one expected scope is required")
        normalized: list[str] = []
        for scope in scopes:
            value = self._required_text(scope, "scope")
            if value not in normalized:
                normalized.append(value)
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            for scope in normalized:
                conn.execute(
                    "INSERT INTO migration_expectations "
                    "(wave_id,ado_project,ado_repo,gh_org,gh_repo,scope,created_at) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(wave_id,ado_project,ado_repo,scope) DO UPDATE SET "
                    "gh_org=excluded.gh_org,gh_repo=excluded.gh_repo,"
                    "created_at=excluded.created_at",
                    (
                        wave_id, repo.ado_project, repo.ado_repo,
                        repo.gh_org, repo.gh_repo, scope, now,
                    ),
                )

    def list_migration_expectations(self, wave_id: int = None) -> list[dict]:
        with self._conn() as conn:
            if wave_id is None:
                rows = conn.execute(
                    "SELECT * FROM migration_expectations ORDER BY id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM migration_expectations WHERE wave_id=? "
                    "ORDER BY id",
                    (wave_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    def upsert_migration(self, wave_id: int, repo: RepoConfig, scope: str,
                         status: MigrationStatus, error: str = None,
                         gh_migration_id: str = None, stats: dict = None):
        now = self._utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO migrations
                    (wave_id, ado_project, ado_repo, gh_org, gh_repo, scope,
                     status, started_at, completed_at, error_message,
                     gh_migration_id, stats)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wave_id, ado_project, ado_repo, scope)
                DO UPDATE SET
                    gh_org          = excluded.gh_org,
                    gh_repo         = excluded.gh_repo,
                    status          = excluded.status,
                    started_at      = COALESCE(migrations.started_at, excluded.started_at),
                    completed_at    = CASE
                        WHEN migrations.status = excluded.status
                             AND excluded.completed_at IS NOT NULL
                        THEN COALESCE(migrations.completed_at, excluded.completed_at)
                        ELSE excluded.completed_at
                    END,
                    error_message   = excluded.error_message,
                    gh_migration_id = excluded.gh_migration_id,
                    stats           = excluded.stats
            """, (
                wave_id, repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
                scope, status.value,
                now if status == MigrationStatus.IN_PROGRESS else None,
                now if status in (MigrationStatus.COMPLETED, MigrationStatus.FAILED,
                                  MigrationStatus.ROLLED_BACK) else None,
                error, gh_migration_id,
                self._json_dumps(stats) if stats is not None else None,
            ))

    def get_wave_migrations(self, wave_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM migrations WHERE wave_id=? ORDER BY id", (wave_id,)
            ).fetchall()]

    def get_all_migrations(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM migrations ORDER BY wave_id, id"
            ).fetchall()]

    def get_failed_migrations(self, wave_id: int = None) -> list[dict]:
        with self._conn() as conn:
            if wave_id is not None:
                return [dict(r) for r in conn.execute(
                    "SELECT * FROM migrations WHERE wave_id=? AND status='failed'",
                    (wave_id,)
                ).fetchall()]
            return [dict(r) for r in conn.execute(
                "SELECT * FROM migrations WHERE status='failed'"
            ).fetchall()]

    def wave_summary(self, wave_id: int) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT scope, status, COUNT(*) as cnt FROM migrations "
                "WHERE wave_id=? GROUP BY scope, status", (wave_id,)
            ).fetchall()
        result: dict = {}
        for r in rows:
            result.setdefault(r["scope"], {})[r["status"]] = r["cnt"]
        return result

    def mark_wave_run(self, wave_id: int, status: str, dry_run: bool = False,
                      run_id: int = None) -> int:
        now = self._utcnow()
        with self.transaction(immediate=True) as conn:
            if status == "started":
                if run_id is not None:
                    raise ValueError("run_id cannot be supplied when starting a wave")
                # Calling start again after a process interruption resumes the
                # same logical run instead of creating duplicate active rows.
                existing = conn.execute(
                    "SELECT id FROM wave_runs WHERE wave_id=? "
                    "AND completed_at IS NULL ORDER BY id DESC LIMIT 1",
                    (wave_id,),
                ).fetchone()
                if existing:
                    return int(existing["id"])
                cur = conn.execute(
                    "INSERT INTO wave_runs (wave_id, started_at, status, dry_run) "
                    "VALUES (?,?,?,?)", (wave_id, now, "in_progress", int(dry_run))
                )
                return cur.lastrowid
            else:
                if run_id is None:
                    conn.execute(
                        "UPDATE wave_runs SET completed_at=?, status=? "
                        "WHERE id=(SELECT id FROM wave_runs WHERE wave_id=? "
                        "          AND completed_at IS NULL "
                        "          ORDER BY id DESC LIMIT 1)",
                        (now, status, wave_id),
                    )
                else:
                    conn.execute(
                        "UPDATE wave_runs SET completed_at=?, status=? "
                        "WHERE id=? AND wave_id=? AND completed_at IS NULL",
                        (now, status, run_id, wave_id),
                    )
                return -1

    # ── Pipeline inventory ──────────────────────────────────────────────────

    def start_pipeline_inventory_run(
        self,
        project: str,
        *,
        include_releases: bool = True,
        ruleset_version: str = "",
    ) -> str:
        project = self._required_text(project, "project")
        run_id = f"inventory_{uuid.uuid4().hex}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_inventory_runs "
                "(run_id,project,status,include_releases,ruleset_version,started_at) "
                "VALUES (?,?, 'in_progress', ?, ?, ?)",
                (
                    run_id,
                    project,
                    int(bool(include_releases)),
                    str(ruleset_version or ""),
                    self._utcnow(),
                ),
            )
        return run_id

    def finish_pipeline_inventory_run(
        self,
        run_id: str,
        *,
        status: str,
        build_count: int = 0,
        release_count: int = 0,
        failed_count: int = 0,
        unmapped_count: int = 0,
        error: str = "",
    ) -> bool:
        run_id = self._required_text(run_id, "run_id")
        status = self._required_text(status, "status")
        if status not in {"completed", "partial", "failed"}:
            raise ValueError("inventory run status must be completed, partial, or failed")
        counts = (build_count, release_count, failed_count, unmapped_count)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in counts):
            raise ValueError("inventory run counts must be non-negative integers")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE pipeline_inventory_runs SET status=?,build_count=?,"
                "release_count=?,total_count=?,failed_count=?,unmapped_count=?,"
                "error_message=?,completed_at=? "
                "WHERE run_id=? AND completed_at IS NULL",
                (
                    status,
                    build_count,
                    release_count,
                    build_count + release_count,
                    failed_count,
                    unmapped_count,
                    str(error or "") or None,
                    self._utcnow(),
                    run_id,
                ),
            )
            return cur.rowcount == 1

    def get_latest_pipeline_inventory_run(self, project: str) -> Optional[dict]:
        project = self._required_text(project, "project")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pipeline_inventory_runs WHERE project=? "
                "ORDER BY started_at DESC,run_id DESC LIMIT 1",
                (project,),
            ).fetchone()
            return dict(row) if row else None

    def get_pipeline_inventory_receipts(
        self, project: str, repo_name: str
    ) -> list[dict]:
        """Return stable semantic fingerprints suitable for an approved plan."""
        return self._get_pipeline_inventory_snapshot(project, repo_name).receipts()

    def get_verified_pipeline_inventory_snapshot(
        self,
        project: str,
        repo_name: str,
        expected_digest: str,
    ) -> PipelineInventorySnapshot:
        """Atomically load metadata only when it matches an approved digest.

        The receipt fields and normalized metadata originate from the same
        SQLite result set.  A concurrent inventory replacement may happen
        before or after that SELECT, but cannot produce a receipt from one
        generation and executable metadata from another.
        """
        expected_digest = self._required_text(
            expected_digest, "expected inventory digest"
        )
        snapshot = self._get_pipeline_inventory_snapshot(project, repo_name)
        if not hmac.compare_digest(snapshot.inventory_digest, expected_digest):
            raise RuntimeError(
                f"Pipeline inventory drift for {project}/{repo_name}: "
                f"planned {expected_digest[:12]}, "
                f"observed {snapshot.inventory_digest[:12]}"
            )
        return snapshot

    def _get_pipeline_inventory_snapshot(
        self, project: str, repo_name: str
    ) -> PipelineInventorySnapshot:
        project = self._required_text(project, "project")
        repo_name = self._required_text(repo_name, "repo_name")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT pipeline_id,pipeline_name,pipeline_type,repo_id,repo_name,"
                "source_yaml_path,source_yaml_sha256,metadata_schema_version,"
                "inventory_ruleset_version,metadata_json "
                "FROM pipeline_inventory WHERE project=? AND repo_name=? "
                "ORDER BY pipeline_id,pipeline_type",
                (project, repo_name),
            ).fetchall()
        receipts: list[dict] = []
        metadata_payloads: list[str] = []
        volatile = {
            "last_run_id", "last_run_result", "last_run_date",
            "avg_duration_min", "total_runs_30d",
        }
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                raise RuntimeError(
                    f"Pipeline inventory metadata is corrupt for "
                    f"{project}/{repo_name} pipeline {row['pipeline_id']}"
                )
            if not isinstance(metadata, dict):
                raise RuntimeError(
                    f"Pipeline inventory metadata is not an object for "
                    f"{project}/{repo_name} pipeline {row['pipeline_id']}"
                )
            identity = (
                int(metadata.get("pipeline_id", -1)),
                str(metadata.get("pipeline_type", "")),
                str(metadata.get("project", "")),
                str(metadata.get("repo_name", "")),
            )
            stored_identity = (
                int(row["pipeline_id"]),
                str(row["pipeline_type"]),
                project,
                repo_name,
            )
            if identity != stored_identity:
                raise RuntimeError(
                    f"Pipeline inventory identity mismatch for "
                    f"{project}/{repo_name} pipeline {row['pipeline_id']}"
                )
            semantic = {
                key: value for key, value in metadata.items()
                if key not in volatile
            }
            receipts.append({
                "pipeline_id": int(row["pipeline_id"]),
                "pipeline_name": str(row["pipeline_name"]),
                "pipeline_type": str(row["pipeline_type"]),
                "repo_id": str(row["repo_id"]),
                "repo_name": str(row["repo_name"]),
                "source_yaml_path": str(row["source_yaml_path"]),
                "source_yaml_sha256": str(row["source_yaml_sha256"]),
                "source_revision": int(metadata.get("source_revision", 0) or 0),
                "metadata_schema_version": int(row["metadata_schema_version"]),
                "inventory_ruleset_version": str(row["inventory_ruleset_version"]),
                "semantic_metadata_sha256": self._digest(semantic),
            })
            metadata_payloads.append(self._json_dumps(metadata))

        receipts_json = json.dumps(
            receipts,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return PipelineInventorySnapshot(
            project=project,
            repo_name=repo_name,
            inventory_digest=hashlib.sha256(
                receipts_json.encode("utf-8")
            ).hexdigest(),
            receipts_json=receipts_json,
            pipeline_metadata_json=tuple(metadata_payloads),
        )

    def upsert_pipeline_inventory(self, meta: PipelineMetadata,
                                  metadata_schema_version: int = 1,
                                  ruleset_version: str = ""):
        now = self._utcnow()
        if isinstance(metadata_schema_version, bool) \
                or not isinstance(metadata_schema_version, int) \
                or metadata_schema_version < 1:
            raise ValueError("metadata_schema_version must be a positive integer")
        source_yaml = meta.yaml_content or ""
        source_yaml_sha256 = self._digest(source_yaml) if source_yaml else ""
        metadata_payload = self._redact_sensitive_payload(meta.to_dict())
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO pipeline_inventory
                    (project, pipeline_id, pipeline_name, pipeline_type,
                     repo_id, repo_name, folder, complexity, metadata_json, scanned_at,
                     source_yaml_path,source_yaml_sha256,metadata_schema_version,
                     inventory_ruleset_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project, pipeline_id, pipeline_type)
                DO UPDATE SET
                    pipeline_name = excluded.pipeline_name,
                    pipeline_type = excluded.pipeline_type,
                    repo_id       = excluded.repo_id,
                    repo_name     = excluded.repo_name,
                    folder        = excluded.folder,
                    complexity    = excluded.complexity,
                    metadata_json = excluded.metadata_json,
                    scanned_at    = excluded.scanned_at,
                    source_yaml_path = excluded.source_yaml_path,
                    source_yaml_sha256 = excluded.source_yaml_sha256,
                    metadata_schema_version = excluded.metadata_schema_version,
                    inventory_ruleset_version = excluded.inventory_ruleset_version
            """, (
                meta.project, meta.pipeline_id, meta.pipeline_name,
                meta.pipeline_type.value, meta.repo_id, meta.repo_name,
                meta.folder, meta.complexity.value,
                self._json_dumps(metadata_payload), now, meta.yaml_path,
                source_yaml_sha256, metadata_schema_version,
                str(ruleset_version or ""),
            ))

    def get_pipelines_for_repo(self, project: str, repo_name: str) -> list[PipelineMetadata]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT metadata_json FROM pipeline_inventory "
                "WHERE project=? AND repo_name=? "
                "ORDER BY pipeline_id,pipeline_type",
                (project, repo_name)
            ).fetchall()
        return [PipelineMetadata.from_dict(json.loads(r["metadata_json"])) for r in rows]

    def get_all_inventory(self, project: str = None) -> list[dict]:
        with self._conn() as conn:
            if project:
                rows = conn.execute(
                    "SELECT * FROM pipeline_inventory WHERE project=? "
                    "ORDER BY pipeline_id,pipeline_type",
                    (project,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pipeline_inventory "
                    "ORDER BY project,pipeline_id,pipeline_type"
                ).fetchall()
        return [dict(r) for r in rows]

    def inventory_count(self, project: str = None) -> int:
        with self._conn() as conn:
            if project:
                return conn.execute(
                    "SELECT COUNT(*) FROM pipeline_inventory WHERE project=?", (project,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM pipeline_inventory").fetchone()[0]

    def inventory_count_for_repo(self, project: str, repo_name: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM pipeline_inventory WHERE project=? AND repo_name=?",
                (project, repo_name)
            ).fetchone()[0]

    def clear_inventory(self, project: str = None):
        with self._conn() as conn:
            if project:
                conn.execute("DELETE FROM pipeline_inventory WHERE project=?", (project,))
            else:
                conn.execute("DELETE FROM pipeline_inventory")

    # ── Pipeline migrations ─────────────────────────────────────────────────

    def upsert_pipeline_migration(self, wave_id: int, meta: PipelineMetadata,
                                  gh_org: str, gh_repo: str,
                                  status: MigrationStatus,
                                  workflow_file: str = None,
                                  error: str = None,
                                  warnings: list = None,
                                  unsupported: list = None,
                                  transform_stats: dict = None,
                                  pev_plan_id: str = "",
                                  pev_run_id: str = "",
                                  source_fingerprint: str = ""):
        pev_plan_id = str(pev_plan_id or "")
        pev_run_id = str(pev_run_id or "")
        source_fingerprint = str(source_fingerprint or "")
        if bool(pev_plan_id) != bool(pev_run_id):
            raise ValueError(
                "pipeline migration PEV plan and run ids must be supplied together"
            )
        now = self._utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO pipeline_migrations
                    (wave_id, project, pipeline_id, pipeline_type,
                     pipeline_name, repo_name,
                     gh_org, gh_repo, workflow_file, status,
                     started_at, completed_at, error_message,
                     warnings, unsupported_tasks, complexity, transform_stats,
                     pev_plan_id,pev_run_id,source_fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(
                    wave_id,project,pipeline_id,pipeline_type,pev_plan_id,pev_run_id
                )
                DO UPDATE SET
                    pipeline_name     = excluded.pipeline_name,
                    repo_name         = excluded.repo_name,
                    gh_org            = excluded.gh_org,
                    gh_repo           = excluded.gh_repo,
                    status            = excluded.status,
                    workflow_file     = excluded.workflow_file,
                    started_at        = COALESCE(pipeline_migrations.started_at,
                                                 excluded.started_at),
                    completed_at      = CASE
                        WHEN pipeline_migrations.status = excluded.status
                             AND excluded.completed_at IS NOT NULL
                        THEN COALESCE(pipeline_migrations.completed_at,
                                      excluded.completed_at)
                        ELSE excluded.completed_at
                    END,
                    error_message     = excluded.error_message,
                    warnings          = excluded.warnings,
                    unsupported_tasks = excluded.unsupported_tasks,
                    complexity        = excluded.complexity,
                    transform_stats   = excluded.transform_stats,
                    pev_plan_id       = excluded.pev_plan_id,
                    pev_run_id        = excluded.pev_run_id,
                    source_fingerprint= excluded.source_fingerprint
            """, (
                wave_id, meta.project, meta.pipeline_id, meta.pipeline_type.value,
                meta.pipeline_name, meta.repo_name, gh_org, gh_repo, workflow_file,
                status.value,
                now if status == MigrationStatus.IN_PROGRESS else None,
                now if status in (
                    MigrationStatus.COMPLETED,
                    MigrationStatus.FAILED,
                    MigrationStatus.NEEDS_REVIEW,
                ) else None,
                error,
                self._json_dumps(warnings or []),
                self._json_dumps(unsupported or []),
                meta.complexity.value,
                self._json_dumps(transform_stats or {}),
                pev_plan_id, pev_run_id, source_fingerprint,
            ))

    def get_wave_pipeline_migrations(
        self,
        wave_id: int,
        *,
        pev_plan_id: str = None,
        pev_run_id: str = None,
    ) -> list[dict]:
        if bool(pev_plan_id) != bool(pev_run_id):
            raise ValueError("pipeline receipt plan and run filters must be paired")
        with self._conn() as conn:
            if pev_plan_id:
                rows = conn.execute(
                    "SELECT * FROM pipeline_migrations WHERE wave_id=? "
                    "AND pev_plan_id=? AND pev_run_id=? ORDER BY id",
                    (wave_id, pev_plan_id, pev_run_id),
                ).fetchall()
            else:
                # Compatibility reports show the latest attempt for each
                # pipeline identity while exact PEV callers request a run.
                rows = conn.execute(
                    "WITH ranked AS (SELECT p.*,ROW_NUMBER() OVER ("
                    "PARTITION BY wave_id,project,pipeline_id,pipeline_type "
                    "ORDER BY COALESCE(completed_at,started_at,'') DESC,id DESC) "
                    "AS rn FROM pipeline_migrations p "
                    "WHERE wave_id=?) SELECT * FROM ranked WHERE rn=1 ORDER BY id",
                    (wave_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_failed_pipeline_migrations(self, wave_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "WITH ranked AS (SELECT p.*,ROW_NUMBER() OVER ("
                "PARTITION BY wave_id,project,pipeline_id,pipeline_type "
                "ORDER BY COALESCE(completed_at,started_at,'') DESC,id DESC) "
                "AS rn FROM pipeline_migrations p "
                "WHERE wave_id=?) SELECT * FROM ranked WHERE rn=1 "
                "AND status IN ('failed','pending','needs_review') ORDER BY id",
                (wave_id,)
            ).fetchall()]

    def pipeline_migration_summary(self, wave_id: int) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "WITH ranked AS (SELECT status,complexity,id,ROW_NUMBER() OVER ("
                "PARTITION BY wave_id,project,pipeline_id,pipeline_type "
                "ORDER BY COALESCE(completed_at,started_at,'') DESC,id DESC) AS rn "
                "FROM pipeline_migrations WHERE wave_id=?) "
                "SELECT status,complexity,COUNT(*) AS cnt FROM ranked WHERE rn=1 "
                "GROUP BY status,complexity", (wave_id,)
            ).fetchall()
        result: dict = {"by_status": {}, "by_complexity": {}}
        for r in rows:
            result["by_status"][r["status"]] = \
                result["by_status"].get(r["status"], 0) + r["cnt"]
            result["by_complexity"][r["complexity"]] = \
                result["by_complexity"].get(r["complexity"], 0) + r["cnt"]
        return result

    def reset_failed_pipeline_migrations(self, wave_id: int):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM pipeline_migrations WHERE wave_id=? "
                "AND pev_plan_id='' AND pev_run_id='' "
                "AND status IN ('failed','needs_review')",
                (wave_id,)
            )

    # ── Risk scores ─────────────────────────────────────────────────────────

    def upsert_risk_score(self, score):
        now = self._utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO repo_risk_scores
                    (project,repo_name,total_score,assigned_phase,gh_org,gh_repo,score_json,scored_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(project,repo_name) DO UPDATE SET
                    total_score=excluded.total_score,
                    assigned_phase=excluded.assigned_phase,
                    gh_org=excluded.gh_org, gh_repo=excluded.gh_repo,
                    score_json=excluded.score_json, scored_at=excluded.scored_at
            """, (
                score.project, score.repo_name, score.total_score,
                score.assigned_phase.value if score.assigned_phase else None,
                score.gh_org, score.gh_repo,
                self._json_dumps(score.to_dict()), now,
            ))

    def get_all_risk_scores(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM repo_risk_scores ORDER BY total_score"
            ).fetchall()]

    def get_risk_scores_for_phase(self, phase) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM repo_risk_scores WHERE assigned_phase=? ORDER BY total_score",
                (phase.value,)
            ).fetchall()]

    def risk_score_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM repo_risk_scores").fetchone()[0]

    # ── Phase gates ─────────────────────────────────────────────────────────

    def upsert_phase_gate(self, result):
        now = self._utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO phase_gates
                    (phase,status,repo_success_pct,pipeline_success_pct,
                     repos_completed,repos_total,pipelines_completed,pipelines_total,
                     failures_json,override_reason,checked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(phase) DO UPDATE SET
                    status=excluded.status,
                    repo_success_pct=excluded.repo_success_pct,
                    pipeline_success_pct=excluded.pipeline_success_pct,
                    repos_completed=excluded.repos_completed,
                    repos_total=excluded.repos_total,
                    pipelines_completed=excluded.pipelines_completed,
                    pipelines_total=excluded.pipelines_total,
                    failures_json=excluded.failures_json,
                    override_reason=excluded.override_reason,
                    checked_at=excluded.checked_at
            """, (
                result.phase.value, result.status.value,
                result.repo_success_pct, result.pipeline_success_pct,
                result.repos_completed, result.repos_total,
                result.pipelines_completed, result.pipelines_total,
                self._json_dumps(result.failures), result.override_reason,
                result.checked_at or now,
            ))

    def get_phase_gate(self, phase) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM phase_gates WHERE phase=?",
                               (phase.value,)).fetchone()
            return dict(row) if row else None

    def get_all_phase_gates(self) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM phase_gates ORDER BY rowid"
            ).fetchall()]

    # ── Batch checkpoints ───────────────────────────────────────────────────

    def upsert_batch_checkpoint(self, cp):
        now = self._utcnow()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO batch_checkpoints
                    (phase,batch_num,total_batches,repos_done,repos_total,status,started_at,completed_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(phase,batch_num) DO UPDATE SET
                    total_batches=excluded.total_batches,
                    repos_done=excluded.repos_done,
                    repos_total=excluded.repos_total,
                    status=excluded.status,
                    started_at=COALESCE(batch_checkpoints.started_at,
                                        excluded.started_at),
                    completed_at=excluded.completed_at
            """, (
                cp.phase.value, cp.batch_num, cp.total_batches,
                cp.repos_done, cp.repos_total, cp.status,
                cp.started_at or now, cp.completed_at,
            ))

    def get_batch_checkpoints(self, phase) -> list:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM batch_checkpoints WHERE phase=? ORDER BY batch_num",
                (phase.value,)
            ).fetchall()]

    def get_last_completed_batch(self, phase) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(batch_num) FROM batch_checkpoints WHERE phase=? AND status='completed'",
                (phase.value,)
            ).fetchone()
            return row[0] if row and row[0] is not None else -1
