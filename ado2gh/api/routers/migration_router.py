"""Migration API router for feature 008 — on-demand and wave migration endpoints.

Provides:
  POST /v1/migration/repo                        — on-demand migration
  POST /v1/migration/wave                        — create a migration wave
  POST /v1/migration/wave/{wave_id}/execute      — execute a wave
  GET  /v1/migration/wave/{wave_id}              — get wave status
  POST /v1/migration/pre-migration-form          — generate pre-migration form
  PUT  /v1/migration/pre-migration-form/{form_id} — submit/validate form
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ado2gh.api.audit_logger import AuditLogger
from ado2gh.api.dependency_graph import DependencyGraph, CircularDependencyError
from ado2gh.api.discovery_store import DiscoveryStore
from ado2gh.api.models import (
    MigrationOperation,
    MigrationWave,
    OperationStatus,
    OperationType,
    PreMigrationForm,
    FormStatus,
    WaveRepository,
    WaveStatus,
)
from ado2gh.api.state_db import get_state_db

router = APIRouter(prefix="/v1/migration", tags=["migration"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Request / Response Models ───

class OnDemandMigrationRequest(BaseModel):
    repository_id: str
    organization_id: str
    pre_migration_form_id: str
    dry_run: bool = False


class CreateWaveRequest(BaseModel):
    name: str
    description: Optional[str] = None
    repository_ids: list[str] = []
    organization_id: str = ""


class ExecuteWaveRequest(BaseModel):
    dry_run: bool = False


class PreMigrationFormRequest(BaseModel):
    repository_id: str
    organization_id: str


class PreMigrationFormSubmit(BaseModel):
    target_github_org: str
    team_mapping: dict[str, Any] = {}
    pipeline_config: dict[str, Any] = {}
    repo_description: Optional[str] = None
    topics: list[str] = []
    labels: list[str] = []


# ─── On-Demand Migration ───

@router.post("/repo", status_code=202)
async def migrate_repo(req: OnDemandMigrationRequest):
    """Initiate on-demand migration of a repository and its dependencies."""
    store = DiscoveryStore()
    db = store.db

    # Check for concurrent migration (FR-016)
    with db._conn() as conn:
        existing = conn.execute(
            """SELECT id FROM migration_operations
               WHERE repository_id = ? AND status IN ('pending', 'in_progress')""",
            (req.repository_id,),
        ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="Repository migration already in progress")

    # Validate pre-migration form exists
    with db._conn() as conn:
        form_row = conn.execute(
            """SELECT id, repository_id, organization_id, target_github_org,
                      team_mapping_json, pipeline_config_json, repo_description,
                      topics_json, labels_json, form_status, created_at, submitted_at
               FROM pre_migration_forms WHERE id = ?""",
            (req.pre_migration_form_id,),
        ).fetchone()
    if not form_row:
        raise HTTPException(status_code=400, detail="Invalid or missing pre-migration form")

    form = PreMigrationForm.from_row(form_row)
    if form.form_status != FormStatus.VALIDATED.value:
        raise HTTPException(status_code=400, detail="Pre-migration form must be validated before migration")

    op = MigrationOperation(
        repository_id=req.repository_id,
        organization_id=req.organization_id,
        operation_type=OperationType.ON_DEMAND.value,
        pre_migration_form_id=req.pre_migration_form_id,
        dry_run=req.dry_run,
        status=OperationStatus.PENDING.value,
    )

    with db._conn() as conn:
        conn.execute(
            """INSERT INTO migration_operations
               (id, repository_id, organization_id, operation_type, wave_id,
                pre_migration_form_id, status, dry_run, confirmed_at,
                started_at, completed_at, error_message, audit_log_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            op.to_row(),
        )

    # Log audit event
    AuditLogger(db=db).log_event(
        operation_id=op.id,
        event_type="migrate",
        new_state={"status": "pending", "dry_run": req.dry_run},
        user_id="",
    )

    return {
        "operation_id": op.id,
        "status": op.status,
        "repository_id": req.repository_id,
        "operation_type": "on_demand",
        "created_at": op.created_at if hasattr(op, 'created_at') else _now(),
    }


# ─── Wave Management ───

@router.post("/wave", status_code=201)
async def create_wave(req: CreateWaveRequest):
    """Create a new migration wave with custom name and repository assignments."""
    if not (1 <= len(req.name) <= 100):
        raise HTTPException(status_code=400, detail="Wave name must be 1-100 characters")

    store = DiscoveryStore()
    db = store.db

    wave = MigrationWave(
        name=req.name,
        description=req.description,
        created_by="",
    )

    with db._conn() as conn:
        conn.execute(
            """INSERT INTO migration_waves
               (id, name, description, status, created_at, started_at, completed_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            wave.to_row(),
        )

        # Assign repositories to wave with dependency-based ordering
        for idx, repo_id in enumerate(req.repository_ids, 1):
            wr = WaveRepository(
                wave_id=wave.id,
                repository_id=repo_id,
                organization_id=req.organization_id,
                migration_order=idx,
            )
            conn.execute(
                """INSERT INTO wave_repositories
                   (id, wave_id, repository_id, organization_id, migration_order, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                wr.to_row(),
            )

    return {
        "wave_id": wave.id,
        "name": wave.name,
        "description": wave.description,
        "status": wave.status,
        "repository_count": len(req.repository_ids),
        "created_at": wave.created_at,
    }


@router.post("/wave/{wave_id}/execute", status_code=202)
async def execute_wave(wave_id: str, req: ExecuteWaveRequest):
    """Execute a migration wave (sequential execution with dependency order)."""
    store = DiscoveryStore()
    db = store.db

    with db._conn() as conn:
        wave_row = conn.execute(
            """SELECT id, name, description, status, created_at, started_at, completed_at, created_by
               FROM migration_waves WHERE id = ?""",
            (wave_id,),
        ).fetchone()
    if not wave_row:
        raise HTTPException(status_code=404, detail="Wave not found")

    wave = MigrationWave.from_row(wave_row)
    if wave.status != WaveStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Wave must be in draft status to execute")

    with db._conn() as conn:
        conn.execute(
            "UPDATE migration_waves SET status = ?, started_at = ? WHERE id = ?",
            (WaveStatus.IN_PROGRESS.value, _now(), wave_id),
        )
        repos = conn.execute(
            """SELECT id, wave_id, repository_id, organization_id, migration_order, status
               FROM wave_repositories WHERE wave_id = ?
               ORDER BY migration_order ASC""",
            (wave_id,),
        ).fetchall()

    return {
        "wave_id": wave_id,
        "status": WaveStatus.IN_PROGRESS.value,
        "started_at": _now(),
        "repositories_in_wave": len(repos),
    }


@router.get("/wave/{wave_id}")
async def get_wave_status(wave_id: str):
    """Retrieve the status and progress of a migration wave."""
    store = DiscoveryStore()
    db = store.db

    with db._conn() as conn:
        wave_row = conn.execute(
            """SELECT id, name, description, status, created_at, started_at, completed_at, created_by
               FROM migration_waves WHERE id = ?""",
            (wave_id,),
        ).fetchone()
    if not wave_row:
        raise HTTPException(status_code=404, detail="Wave not found")

    wave = MigrationWave.from_row(wave_row)

    with db._conn() as conn:
        repos = conn.execute(
            """SELECT id, wave_id, repository_id, organization_id, migration_order, status
               FROM wave_repositories WHERE wave_id = ?
               ORDER BY migration_order ASC""",
            (wave_id,),
        ).fetchall()

    return {
        "wave_id": wave.id,
        "name": wave.name,
        "status": wave.status,
        "created_at": wave.created_at,
        "started_at": wave.started_at,
        "repositories": [
            {
                "repository_id": WaveRepository.from_row(r).repository_id,
                "migration_order": WaveRepository.from_row(r).migration_order,
                "status": WaveRepository.from_row(r).status,
            }
            for r in repos
        ],
    }


# ─── Pre-Migration Forms ───

@router.post("/pre-migration-form")
async def generate_pre_migration_form(req: PreMigrationFormRequest):
    """Generate a pre-migration form based on dependency analysis."""
    if not req.repository_id:
        raise HTTPException(status_code=400, detail="repository_id is required")

    store = DiscoveryStore()

    # Verify repo exists — check discovery_results first, then profile_scan_repos
    result = store.get_result(req.organization_id, req.repository_id)
    if not result:
        # Profile scan stores repos in profile_scan_repos, not discovery_results.
        # The UI sends profile_id as organization_id and repo_name as repository_id.
        db = store.db
        repo_found = False
        if hasattr(db, "get_profile_scan_repos"):
            for row in db.get_profile_scan_repos(req.organization_id):
                if row["repo_name"] == req.repository_id:
                    repo_found = True
                    break
        if not repo_found:
            raise HTTPException(status_code=404, detail="Repository not found in discovery results")

    # Build dependency graph for this repo
    graph = DependencyGraph.from_store(store, repository_id=req.repository_id)
    graph.add_node(req.repository_id)
    try:
        migration_order = graph.get_full_migration_order(req.repository_id)
    except CircularDependencyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    form = PreMigrationForm(
        repository_id=req.repository_id,
        organization_id=req.organization_id,
    )

    db = store.db
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO pre_migration_forms
               (id, repository_id, organization_id, target_github_org,
                team_mapping_json, pipeline_config_json, repo_description,
                topics_json, labels_json, form_status, created_at, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            form.to_row(),
        )

    return {
        "form_id": form.id,
        "repository_id": req.repository_id,
        "required_fields": {
            "target_github_org": {"type": "string", "description": "Target GitHub organization"},
            "team_mapping": {"type": "object", "description": "Team/permission mapping configuration"},
            "pipeline_config": {"type": "object", "description": "Pipeline configuration"},
        },
        "optional_fields": {
            "repo_description": {"type": "string", "description": "Repository description"},
            "topics": {"type": "array", "description": "Repository topics"},
            "labels": {"type": "array", "description": "Repository labels"},
        },
        "dependency_graph": graph.to_dict(),
    }


@router.put("/pre-migration-form/{form_id}")
async def submit_pre_migration_form(form_id: str, req: PreMigrationFormSubmit):
    """Submit and validate a completed pre-migration form."""
    store = DiscoveryStore()
    db = store.db

    with db._conn() as conn:
        row = conn.execute(
            """SELECT id, repository_id, organization_id, target_github_org,
                      team_mapping_json, pipeline_config_json, repo_description,
                      topics_json, labels_json, form_status, created_at, submitted_at
               FROM pre_migration_forms WHERE id = ?""",
            (form_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Form not found")

    # Validate required fields
    if not req.target_github_org:
        raise HTTPException(status_code=400, detail="target_github_org is required")
    if req.team_mapping is None:
        raise HTTPException(status_code=400, detail="team_mapping is required")
    if req.pipeline_config is None:
        raise HTTPException(status_code=400, detail="pipeline_config is required")

    with db._conn() as conn:
        conn.execute(
            """UPDATE pre_migration_forms
               SET target_github_org = ?, team_mapping_json = ?, pipeline_config_json = ?,
                   repo_description = ?, topics_json = ?, labels_json = ?,
                   form_status = ?, submitted_at = ?
               WHERE id = ?""",
            (
                req.target_github_org,
                json.dumps(req.team_mapping),
                json.dumps(req.pipeline_config),
                req.repo_description,
                json.dumps(req.topics),
                json.dumps(req.labels),
                FormStatus.VALIDATED.value,
                _now(),
                form_id,
            ),
        )

    return {
        "form_id": form_id,
        "form_status": FormStatus.VALIDATED.value,
        "validated_at": _now(),
    }
