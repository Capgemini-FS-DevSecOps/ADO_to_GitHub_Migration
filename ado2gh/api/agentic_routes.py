"""REST routes for assignments, gates, rollback, audit, and workflow readiness."""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ado2gh.assignments.audit import AuditWriter
from ado2gh.assignments.audit_export import AuditExportJob
from ado2gh.assignments.models import AssignmentType
from ado2gh.assignments.rbac import ProfileRole, RBAC
from ado2gh.assignments.resolver import AssignmentResolver
from ado2gh.assignments.store import AssignmentStore
from ado2gh.api.workflow_readiness import check_workflow_readiness
from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.gh_client import GHClient
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.rollback import RollbackHandler
from ado2gh.models import GateStatus, MigrationScope, PhaseType, RepoConfig, WaveConfig
from ado2gh.phase.gate_checker import PhaseGateChecker
from ado2gh.phase.policy_rules import PolicyEvaluator, PolicyRules
from ado2gh.pipelines.dependency_graph import RepoDependencyEdge, build_graph
from ado2gh.state.factory import create_state_db

router = APIRouter(tags=["agentic"])

_rollback_approvals: dict[str, dict] = {}


def _db_path() -> str:
    return os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db")


def _resolve_phase(execution_phase: str) -> PhaseType:
    low = execution_phase.lower()
    for pt in PhaseType:
        if pt.value == low or low.endswith(pt.value):
            return pt
    return PhaseType.POC


def _gate_payload(assignment_id: str, phase: PhaseType, result, can_override: bool = False) -> dict:
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    can_advance = status in (GateStatus.PASS.value, GateStatus.OVERRIDE.value)
    return {
        "assignment_id": assignment_id,
        "phase": phase.value,
        "status": status,
        "repo_success_pct": result.repo_success_pct,
        "pipeline_success_pct": result.pipeline_success_pct,
        "repos_completed": result.repos_completed,
        "repos_total": result.repos_total,
        "pipelines_completed": result.pipelines_completed,
        "pipelines_total": result.pipelines_total,
        "failures": result.failures,
        "can_advance": can_advance,
        "checked_at": result.checked_at,
    }


def check_assignment_gate(assignment_id: str, db_path: str | None = None) -> dict:
    """Evaluate assignment-scoped gate; used by migrate/jobs enforcement."""
    db = create_state_db(db_path or _db_path())
    store = AssignmentStore(db)
    assignment = store.get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    phase = _resolve_phase(assignment.execution_phase)
    cohort = [r["ado_repo"] for r in assignment.repos]
    checker = PhaseGateChecker(db)
    result = checker.check_for_assignment(phase, cohort)
    existing = db.get_phase_gate(phase)
    if existing and existing.get("status") == GateStatus.OVERRIDE.value:
        result.status = GateStatus.OVERRIDE
    return _gate_payload(assignment_id, phase, result)


class CreateAssignmentBody(BaseModel):
    name: str
    assignment_type: str
    execution_phase_id: str
    repos: list[dict[str, str]]
    wave_number: Optional[int] = None


class GateOverrideBody(BaseModel):
    reason: str
    actor: str = "approver"
    profile_id: str = ""


class RollbackBody(BaseModel):
    assignment_id: Optional[str] = None
    repos: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["pipelines"])
    dry_run: bool = True
    wave_id: Optional[int] = None
    db_path: str = "migration_state.db"
    actor: str = "operator"
    profile_id: str = ""


class RollbackApproveBody(BaseModel):
    request_id: str
    approved: bool = True
    actor: str = "approver"
    reason: str = ""


class WorkflowReadinessBody(BaseModel):
    repo: str
    missing_secrets: list[str] = Field(default_factory=list)
    missing_envs: list[str] = Field(default_factory=list)


@router.post("/v1/profiles/{profile_id}/assignments")
def create_assignment(profile_id: str, body: CreateAssignmentBody, actor: str = "coordinator"):
    if not RBAC.from_actor(actor).has_role(ProfileRole.COORDINATOR):
        raise HTTPException(status_code=403, detail="Coordinator role required")
    db = create_state_db(_db_path())
    store = AssignmentStore(db)
    try:
        atype = AssignmentType(body.assignment_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid assignment_type")
    assignment = store.create(
        profile_id=profile_id,
        name=body.name,
        assignment_type=atype,
        execution_phase=body.execution_phase_id,
        repos=body.repos,
        wave_number=body.wave_number,
        created_by=actor,
    )
    AuditWriter(db).write(
        "assignment_created", profile_id, actor=actor,
        assignment_id=assignment.id, payload={"name": body.name},
    )
    return {
        "id": assignment.id,
        "name": assignment.name,
        "assignment_type": assignment.assignment_type.value,
        "wave_number": assignment.wave_number,
        "execution_phase_id": assignment.execution_phase,
        "repo_count": len(assignment.repos),
        "status": assignment.status,
    }


@router.get("/v1/profiles/{profile_id}/assignments")
def list_assignments(profile_id: str, type: Optional[str] = None):
    db = create_state_db(_db_path())
    store = AssignmentStore(db)
    items = store.list_for_profile(profile_id)
    if type:
        items = [a for a in items if a.assignment_type.value == type]
    return {
        "assignments": [
            {
                "id": a.id,
                "name": a.name,
                "assignment_type": a.assignment_type.value,
                "wave_number": a.wave_number,
                "execution_phase_id": a.execution_phase,
                "repo_count": len(a.repos),
                "status": a.status,
            }
            for a in items
        ]
    }


@router.get("/v1/profiles/{profile_id}/dependency-graph")
def dependency_graph(profile_id: str):
    db = create_state_db(_db_path())
    raw = db.get_dependency_edges(profile_id)
    edges = [
        RepoDependencyEdge(r["from_repo"], r["to_repo"], r.get("edge_type", "pipeline_resource"))
        for r in raw
    ]
    graph = build_graph(edges)
    return {
        "edges": [
            {"from_repo": e.from_repo, "to_repo": e.to_repo, "edge_type": e.edge_type}
            for e in graph.edges
        ],
        "sorted_repos": graph.sorted_repos,
        "cycles": graph.cycles,
    }


@router.post("/v1/workflow-readiness")
def workflow_readiness(body: WorkflowReadinessBody):
    return check_workflow_readiness(body.repo, body.missing_secrets, body.missing_envs)


@router.get("/v1/assignments/{assignment_id}/gate-status")
def assignment_gate_status(assignment_id: str):
    return check_assignment_gate(assignment_id)


@router.post("/v1/assignments/{assignment_id}/gate-override")
def assignment_gate_override(assignment_id: str, body: GateOverrideBody):
    if not RBAC.from_actor(body.actor).has_role(ProfileRole.APPROVER):
        raise HTTPException(status_code=403, detail="Approver role required")
    db = create_state_db(_db_path())
    store = AssignmentStore(db)
    assignment = store.get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    phase = _resolve_phase(assignment.execution_phase)
    cohort = [r["ado_repo"] for r in assignment.repos]
    checker = PhaseGateChecker(db)
    result = checker.check_for_assignment(phase, cohort)
    checker.override(phase, body.reason)
    result.status = GateStatus.OVERRIDE
    event_id = AuditWriter(db).write(
        "gate_override", body.profile_id or assignment.profile_id,
        actor=body.actor, assignment_id=assignment_id,
        payload={"reason": body.reason, "phase": phase.value},
    )
    payload = _gate_payload(assignment_id, phase, result)
    payload["audit_event_id"] = event_id
    return payload


@router.post("/v1/rollback/request")
def rollback_request(body: RollbackBody):
    import uuid
    rid = f"rb_{uuid.uuid4().hex[:10]}"
    _rollback_approvals[rid] = body.model_dump()
    return {"request_id": rid, "status": "pending_approval", "dry_run": body.dry_run}


@router.post("/v1/rollback/approve")
def rollback_approve(body: RollbackApproveBody):
    if not RBAC.from_actor(body.actor).has_role(ProfileRole.APPROVER):
        raise HTTPException(status_code=403, detail="Approver role required")
    pending = _rollback_approvals.get(body.request_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Rollback request not found")
    if not body.approved:
        _rollback_approvals.pop(body.request_id, None)
        return {"status": "rejected", "reason": body.reason}
    rb = RollbackBody(**pending)
    rb.dry_run = False
    result = _execute_rollback(rb)
    _rollback_approvals.pop(body.request_id, None)
    result["approval"] = {"approved": True, "actor": body.actor}
    return result


@router.post("/v1/rollback")
def rollback(body: RollbackBody):
    if not body.dry_run and body.profile_id:
        rules = PolicyRules.from_dict(_policy_rules_for_profile(body.profile_id))
        if rules.rollback_requires_approver:
            raise HTTPException(
                status_code=403,
                detail="Live rollback requires Approver via /v1/rollback/approve",
            )
    return _execute_rollback(body)


def _policy_rules_for_profile(profile_id: str) -> dict:
    try:
        from ado2gh.api.settings_store import SettingsStore
        settings = SettingsStore().load()
        adv = settings.advanced
        return getattr(adv, "policy_rules", {}) or {}
    except Exception:
        return {}


def _execute_rollback(body: RollbackBody) -> dict:
    db = create_state_db(body.db_path)
    audit = AuditWriter(db)
    actions: list[dict] = []
    stats: dict[str, Any] = {}

    if body.dry_run:
        for repo_key in body.repos:
            for scope in body.scopes:
                actions.append({"repo": repo_key, "scope": scope, "action": f"rollback_{scope}"})
                if scope == MigrationScope.PIPELINES.value:
                    actions.append({
                        "repo": repo_key,
                        "scope": scope,
                        "action": "re_enable_ado_pipelines",
                    })
        return {
            "dry_run": True,
            "assignment_id": body.assignment_id,
            "actions": actions,
            "audit_event_id": None,
        }

    global_cfg, waves = ConfigLoader.load(os.environ.get("ADO2GH_CONFIG", "migration.yaml"))
    from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
    gh = _build_gh_client(global_cfg)
    ado = _build_ado_client(global_cfg)
    handler = RollbackHandler(gh, db, ado=ado)

    wave_id = body.wave_id or 1
    wave = next((w for w in waves if w.wave_id == wave_id), waves[0] if waves else None)
    if not wave:
        raise HTTPException(status_code=400, detail="No wave configured")

    repo_configs: list[RepoConfig] = []
    for key in body.repos:
        parts = key.split("/", 1)
        if len(parts) != 2:
            continue
        project, repo = parts
        repo_configs.append(RepoConfig(
            ado_project=project, ado_repo=repo,
            gh_org=global_cfg.get("gh_org", ""), gh_repo=repo,
        ))

    stats = handler.rollback_repos(repo_configs, wave_id, scopes=body.scopes, dry_run=False)
    if MigrationScope.PIPELINES.value in body.scopes and repo_configs:
        for repo in repo_configs:
            for record in db.get_wave_migrations(wave_id):
                if record["ado_repo"] == repo.ado_repo and record["scope"] == MigrationScope.PIPELINES.value:
                    handler._rollback_pipelines(wave_id, record, False, stats)

    event_id = audit.write(
        "rollback", body.profile_id, actor=body.actor,
        assignment_id=body.assignment_id, payload={"scopes": body.scopes, "stats": stats},
    )
    return {
        "dry_run": False,
        "status": "completed",
        "stats": stats,
        "audit_event_id": event_id,
    }


@router.get("/v1/profiles/{profile_id}/audit-events")
def list_audit(profile_id: str, limit: int = 100):
    db = create_state_db(_db_path())
    return {"events": db.list_audit_events(profile_id=profile_id, limit=limit)}


@router.post("/v1/profiles/{profile_id}/audit-export")
def audit_export(profile_id: str, bucket: Optional[str] = None):
    db = create_state_db(_db_path())
    job = AuditExportJob(db, bucket=bucket or os.environ.get("ADO2GH_AUDIT_BUCKET"))
    return job.export_profile(profile_id)


@router.get("/v1/history/sessions")
def history_sessions(profile_id: Optional[str] = None, limit: int = 50):
    """FR-039 — sessions derived from audit + remediation loops."""
    db = create_state_db(_db_path())
    events = db.list_audit_events(profile_id=profile_id, limit=limit)
    sessions = [
        e for e in events
        if e.get("event_type") in ("agent_session", "rollback", "gate_override", "assignment_created")
    ]
    return {"sessions": sessions, "count": len(sessions)}


def enforce_live_gate(assignment_id: str, dry_run: bool, db_path: str) -> None:
    """Raise 409 when live run blocked by assignment gate."""
    if dry_run or not assignment_id:
        return
    payload = check_assignment_gate(assignment_id, db_path)
    if not payload.get("can_advance"):
        raise HTTPException(
            status_code=409,
            detail={"message": "Assignment gate blocked", "gate": payload},
        )
