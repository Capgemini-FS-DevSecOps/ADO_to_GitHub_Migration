"""Pydantic request/response contracts for the Accelerator SDK."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class DiscoverRequest(BaseModel):
    config_path: str
    output_dir: str = "output/discovery"


class DiscoverResult(BaseModel):
    output_dir: str
    projects_scanned: int = 0


class RunWaveRequest(BaseModel):
    config_path: str
    wave_id: Optional[int] = None
    dry_run: bool = False
    db_path: str = "migration_state.db"
    assignment_id: Optional[str] = None
    live_approval_id: Optional[str] = None


class RunWaveResult(BaseModel):
    wave_id: int
    status: str
    completed: int
    failed: int
    total: int
    dry_run: bool = False


class PhaseRunRequest(BaseModel):
    config_path: str
    phase: Literal["poc", "pilot", "wave1", "wave2", "wave3"]
    dry_run: bool = False
    force: bool = False
    db_path: str = "migration_state.db"


class PhaseRunResult(BaseModel):
    phase: str
    completed: int
    failed: int
    batches_run: int
    batches_skipped: int


class ValidateRequest(BaseModel):
    config_path: Optional[str] = None
    config_yaml: Optional[str] = None
    input_path: Optional[str] = None
    input_text: Optional[str] = None
    profile_id: Optional[str] = None
    phase: Optional[str] = None
    db_path: Optional[str] = None
    output_path: str = "output/validation_report.csv"


class ValidateResult(BaseModel):
    total: int
    passed: int
    failed: int
    output_path: Optional[str] = None
    details: list[dict[str, Any]] = Field(default_factory=list)


class StatusSnapshot(BaseModel):
    migrations: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_inventory_count: int = 0


# ── Extended contracts for REST API / job queue ─────────────────────────────

from enum import Enum


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "5.1.0"


class JobTypeEnum(str, Enum):
    DISCOVER = "discover"
    INVENTORY_PROJECT = "inventory_project"
    MIGRATE_REPO = "migrate_repo"
    TRANSFORM_PIPELINE = "transform_pipeline"
    VALIDATE_REPO = "validate_repo"
    PUSH_WORKFLOWS = "push_workflows"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobEnqueueRequest(BaseModel):
    job_type: JobTypeEnum
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None


class JobRecord(BaseModel):
    id: str
    job_type: JobTypeEnum
    status: JobStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    idempotency_key: Optional[str] = None


class JobStatusResponse(BaseModel):
    job: JobRecord


class PlanRequest(BaseModel):
    config_path: str
    wave_id: Optional[int] = None
    db_path: str = "migration_state.db"


class PlanResponse(BaseModel):
    waves: list[dict[str, Any]] = Field(default_factory=list)


class DiscoverResponse(BaseModel):
    projects: int = 0
    repos: int = 0
    pipelines: int = 0
    output_dir: str = ""


class RunWaveResponse(BaseModel):
    wave_id: int
    status: str
    completed: int = 0
    failed: int = 0
    partial: int = 0
    total: int = 0
    elapsed_sec: float = 0.0


class ValidateResponse(BaseModel):
    total: int = 0
    matched: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)


class ReadinessRequest(BaseModel):
    config_path: str = "migration.yaml"
    db_path: str = "migration_state.db"
    refresh_inventory: bool = False


class ReadinessResponse(BaseModel):
    auto: int = 0
    assisted: int = 0
    manual: int = 0
    total_pipelines: int = 0
    total_effort_hours: float = 0.0
    inventory_refreshed: bool = False
    pipelines: list[dict[str, Any]] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    total_repos: int = 0
    completed_repos: int = 0
    failed_repos: int = 0
    total_pipelines: int = 0
    inventory_count: int = 0
    phase_gates: list[dict[str, Any]] = Field(default_factory=list)
    active_migrations: list["ActiveMigrationItem"] = Field(default_factory=list)


class ActiveMigrationItem(BaseModel):
    id: str
    name: str
    status: str
    dry_run: bool = True
    phase: str = "poc"
    wave_id: Optional[int] = None
    current_step: str = ""
    started_by_username: str = ""
    started_by_display_name: str = ""
    created_at: str = ""
    updated_at: str = ""


class FreshnessRequest(BaseModel):
    config_path: str
    project: str
    repo: str
    db_path: str = "migration_state.db"


class FreshnessResponse(BaseModel):
    project: str
    repo: str
    ado_head_sha: str = ""
    gh_head_sha: str = ""
    fresh: bool = False


class GitHubTokenRequest(BaseModel):
    name: str
    token: str = ""
    note: str = ""


class GitHubTokenResponse(BaseModel):
    id: str
    name: str
    token: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str = ""
    last_validation: dict[str, Any] = Field(default_factory=dict)


class MigrationProfileRequest(BaseModel):
    name: str
    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""


class MigrationProfileResponse(BaseModel):
    id: str
    name: str
    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""
    github_tokens: list[GitHubTokenResponse] = Field(default_factory=list)
    status: str = "active"
    is_default: bool = False
    submitted_by: str = ""
    approval: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_scan_at: str = ""
    scan_summary: dict[str, Any] = Field(default_factory=dict)


class OnboardingStatusResponse(BaseModel):
    needs_profile_setup: bool = False
    active_profile_count: int = 0
    default_profile_id: Optional[str] = None
    pending_approval_count: int = 0
    role: str = ""
    blocked_message: Optional[str] = None
    redirect_path: Optional[str] = None
    can_submit_profile: bool = False


class DeleteProfileRequest(BaseModel):
    new_default_profile_id: Optional[str] = None


class DenyProfileRequest(BaseModel):
    reason: str = ""


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""


class ProfileSetupRequest(BaseModel):
    name: str
    ado_org_url: str
    ado_pat: str
    gh_org: str
    github_token: str
    github_token_name: str = "Primary"


class MigrationScanRequest(BaseModel):
    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""
    max_repos: Optional[int] = None


class PhaseRecommendation(BaseModel):
    phase: str
    repo_count: int = 0
    risk_min: float = 0.0
    risk_max: float = 0.0
    rationale: str = ""
    repos: list[dict[str, Any]] = Field(default_factory=list)


class MigrationScanResponse(BaseModel):
    scanned_at: str = ""
    projects_scanned: int = 0
    repos_scanned: int = 0
    total_repos: int = 0
    gh_org: str = ""
    recommendations: dict[str, PhaseRecommendation] = Field(default_factory=dict)
    project_details: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "ok"


# Legacy aliases kept for internal imports
ConnectionProfileRequest = MigrationProfileRequest
ConnectionProfileResponse = MigrationProfileResponse


class ValidateGitHubTokenRequest(BaseModel):
    token: str = ""
    gh_org: str = ""


class ValidateGitHubTokenResponse(BaseModel):
    valid: bool
    message: str = ""
    login: str = ""
    scopes: list[str] = Field(default_factory=list)
    remaining: int = 0
    reset_at: int = 0
    org_accessible: Optional[bool] = None
    warnings: list[str] = Field(default_factory=list)


class ValidateAdoPatRequest(BaseModel):
    ado_org_url: str = ""
    ado_pat: str = ""


class ValidateAdoPatResponse(BaseModel):
    valid: bool
    message: str = ""
    ado_projects: int = 0


class SettingsResponse(BaseModel):
    active_profile_id: Optional[str] = None
    migration_profiles: list[MigrationProfileResponse] = Field(default_factory=list)
    advanced: dict[str, Any] = Field(default_factory=dict)


class AdvancedSettingsRequest(BaseModel):
    config_path: Optional[str] = None
    db_path: Optional[str] = None
    dry_run_default: Optional[bool] = None
    migration_strategy: Optional[str] = None
    default_phase: Optional[str] = None
    repo_parallel: Optional[int] = None
    pipeline_parallel: Optional[int] = None
    output_dir: Optional[str] = None


class PhaseDefinitionItem(BaseModel):
    id: str
    name: str
    risk_max: float
    repo_cap: int = 9999
    order: int = 0


class PhaseRemovalItem(BaseModel):
    phase_id: str
    move_repos_to: str


class PhasesUpdateRequest(BaseModel):
    phases: list[PhaseDefinitionItem]
    removals: list[PhaseRemovalItem] = Field(default_factory=list)
    span_to_scan: bool = False
    profile_id: Optional[str] = None


class ValidateConnectionResponse(BaseModel):
    valid: bool
    ado_projects: int = 0
    gh_token_remaining: int = 0
    message: str = ""


class PipelineRunStartRequest(BaseModel):
    name: str = "Manual migration"
    dry_run: bool = True
    phase: str = "poc"
    wave_id: Optional[int] = None
    steps: Optional[list[str]] = None


class PipelineRunResponse(BaseModel):
    run: dict[str, Any]


class PipelineStepDefinition(BaseModel):
    id: str
    label: str
    description: str


class PhaseAssignmentItem(BaseModel):
    project: str
    repo_name: str
    assigned_phase: str


class PhaseAssignmentRequest(BaseModel):
    assignments: list[PhaseAssignmentItem] = Field(default_factory=list)


class DiscoveryRepoItem(BaseModel):
    project: str
    repo_name: str
    total_score: float = 0
    suggested_phase: Optional[str] = None
    assigned_phase: Optional[str] = None
    gh_org: str = ""
    gh_repo: str = ""
    pipeline_count: int = 0


class DiscoveryResponse(BaseModel):
    profile_id: str
    scanned_at: str = ""
    gh_org: str = ""
    repos_scanned: int = 0
    projects_scanned: int = 0
    repos: list[DiscoveryRepoItem] = Field(default_factory=list)
    recommendations: dict[str, Any] = Field(default_factory=dict)
    project_details: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "ok"


class LiveApprovalCreateRequest(BaseModel):
    scope_type: Literal["agent_session", "migrate_job", "pipeline_run"]
    scope_id: str
    profile_id: Optional[str] = None
    assignment_id: Optional[str] = None
    reason_request: Optional[str] = None
    context: dict[str, Any] = Field(default_factory=dict)


class LiveApprovalDecisionRequest(BaseModel):
    reason: str = Field(min_length=1)


class LiveApprovalItem(BaseModel):
    id: str
    requester_username: str
    scope_type: str
    scope_id: str
    profile_id: Optional[str] = None
    assignment_id: Optional[str] = None
    status: str
    reason_request: Optional[str] = None
    reason_decision: Optional[str] = None
    requested_at: str
    decided_at: Optional[str] = None
    approver_username: Optional[str] = None


class LiveApprovalListResponse(BaseModel):
    approvals: list[LiveApprovalItem] = Field(default_factory=list)
