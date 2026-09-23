"""Pydantic request/response contracts for the Accelerator SDK."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ado2gh.models import JobRecord, JobTypeEnum


class DiscoverRequest(BaseModel):
    """Scan an ADO organization for migratable repositories and pipelines; body of ``POST /v1/discover``."""

    config_path: str
    output_dir: str = "output/discovery"


class DiscoverResult(BaseModel):
    """Report where ``Accelerator.discover`` wrote its artifacts and how many projects it scanned."""

    output_dir: str
    projects_scanned: int = 0


class RunWaveRequest(BaseModel):
    """Migrate one wave, or every wave in the configuration; body of ``POST /v1/migrate``.

    ``dry_run`` defaults to true, so a body that omits it only previews; a caller
    has to say ``dry_run: false`` to migrate for real and push into GitHub. A preview
    run being the default and a real run needing an explicit opt-in is a standing
    safeguard across this codebase (register cross-reference: CA-001).
    An operator without live-execution rights is refused with ``awaiting_approval``;
    ``live_approval_id`` then replays the request against the approval that was granted.
    """

    config_path: str
    wave_id: Optional[int] = None
    dry_run: bool = True
    db_path: str = "migration_state.db"
    live_approval_id: Optional[str] = None


class RunWaveResult(BaseModel):
    """Summarise one executed wave; a ``completed`` count under ``dry_run`` means nothing reached GitHub."""

    wave_id: int
    status: str
    completed: int
    failed: int
    total: int
    dry_run: bool = False


class PhaseRunRequest(BaseModel):
    """Execute one risk-based migration phase with its gates enforced, behind ``ado2gh phase run``.

    ``dry_run`` defaults to true, so a request that omits it only previews; a caller
    has to say ``dry_run: false`` to migrate for real. A preview run being the default
    and a real run needing an explicit opt-in is a standing safeguard across this
    codebase (register cross-reference: CA-001). ``force`` bypasses a blocking prior-phase
    gate and belongs with ``override_reason``, which is persisted on the OVERRIDE record as the
    audit trail for the escalation.
    """

    config_path: str
    phase: Literal["poc", "pilot", "wave1", "wave2", "wave3"]
    dry_run: bool = True
    force: bool = False
    override_reason: str = Field(
        default="",
        description="Escalation justification, required when `force` bypasses a "
                    "blocking prior-phase gate; persisted on the OVERRIDE record.",
    )
    db_path: str = "migration_state.db"


class PhaseRunResult(BaseModel):
    """Summarise a phase run; a non-zero ``batches_skipped`` means a gate stopped work that never ran."""

    phase: str
    completed: int
    failed: int
    batches_run: int
    batches_skipped: int


class ValidateRequest(BaseModel):
    """Verify migrated repositories against ADO at commit-SHA level; body of ``POST /v1/validate``.

    The repository set comes from the first source that yields anything, later ones then ignored:
    ``input_text``, ``input_path``, the ``phase`` of the active profile, then the wave config.
    """

    config_path: Optional[str] = None
    config_yaml: Optional[str] = None
    input_path: Optional[str] = None
    input_text: Optional[str] = None
    profile_id: Optional[str] = None
    phase: Optional[str] = None
    db_path: Optional[str] = None
    output_path: str = "output/validation_report.csv"


class ValidateResult(BaseModel):
    """Report how many repositories matched their ADO source at HEAD commit SHA."""

    total: int
    passed: int
    failed: int
    output_path: Optional[str] = None
    details: list[dict[str, Any]] = Field(default_factory=list)


class StatusSnapshot(BaseModel):
    """Report the migration state recorded in the state database, contacting neither ADO nor GitHub."""

    migrations: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_inventory_count: int = 0


# ── Extended contracts for REST API / job queue ─────────────────────────────


class HealthResponse(BaseModel):
    """Report that the Accelerator API is up; ``GET /health`` checks no dependencies of its own."""

    status: str = "ok"
    version: str = "5.1.0"


class JobEnqueueRequest(BaseModel):
    """Queue a background migration job; body of ``POST /v1/jobs``.

    ``payload`` carries the job-type arguments, including the ``dry_run`` flag that decides whether
    the job rehearses or writes for real. ``idempotency_key`` makes a retry return the job already
    queued instead of enqueuing a second one.
    """

    job_type: JobTypeEnum
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Wrap one job record for ``POST /v1/jobs`` and ``GET /v1/jobs/{job_id}``."""

    job: JobRecord


class PlanRequest(BaseModel):
    """Preview the waves a configuration would run without running them; body of the deprecated ``POST /v1/plan``."""

    config_path: str
    wave_id: Optional[int] = None
    db_path: str = "migration_state.db"


class PlanResponse(BaseModel):
    """List the waves a plan preview resolved; nothing is migrated and no run is recorded."""

    waves: list[dict[str, Any]] = Field(default_factory=list)


class DiscoverResponse(BaseModel):
    """Report the project, repository and pipeline totals of ``POST /v1/discover`` and where they landed."""

    projects: int = 0
    repos: int = 0
    pipelines: int = 0
    output_dir: str = ""


class RunWaveResponse(BaseModel):
    """Report a wave's outcome over HTTP; ``partial`` counts repos migrated with some scopes failing."""

    wave_id: int
    status: str
    completed: int = 0
    failed: int = 0
    partial: int = 0
    total: int = 0
    elapsed_sec: float = 0.0


class ValidateResponse(BaseModel):
    """Report commit-level validation over HTTP; ``matched`` is the evidence that code actually transferred."""

    total: int = 0
    matched: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)


class ReadinessRequest(BaseModel):
    """Assess pipeline conversion effort; ``refresh_inventory`` re-scans ADO first, as an empty inventory does."""

    config_path: str = "migration.yaml"
    db_path: str = "migration_state.db"
    refresh_inventory: bool = False


class ReadinessResponse(BaseModel):
    """Split inventoried pipelines into automatic, assisted and manual work with an effort estimate."""

    auto: int = 0
    assisted: int = 0
    manual: int = 0
    total_pipelines: int = 0
    total_effort_hours: float = 0.0
    inventory_refreshed: bool = False
    pipelines: list[dict[str, Any]] = Field(default_factory=list)


class DashboardSnapshot(BaseModel):
    """Give ``GET /v1/dashboard`` its repository and pipeline totals, phase gates and in-flight runs."""

    total_repos: int = 0
    completed_repos: int = 0
    failed_repos: int = 0
    total_pipelines: int = 0
    inventory_count: int = 0
    phase_gates: list[dict[str, Any]] = Field(default_factory=list)
    active_migrations: list["ActiveMigrationItem"] = Field(default_factory=list)


class ActiveMigrationItem(BaseModel):
    """Describe one migration run currently in flight for the dashboard.

    ``dry_run`` separates a rehearsal from a run that is really writing to GitHub, and the
    ``started_by_*`` fields attribute it to the operator who launched it.
    """

    id: str
    name: str
    status: str
    dry_run: bool = True
    phase: str = ""
    wave_id: Optional[int] = None
    repository_id: Optional[str] = None
    current_step: str = ""
    started_by_username: str = ""
    started_by_display_name: str = ""
    created_at: str = ""
    updated_at: str = ""


class FreshnessRequest(BaseModel):
    """Check one migrated repository against its ADO source; body of ``POST /v1/validate/freshness``."""

    config_path: str
    project: str
    repo: str
    db_path: str = "migration_state.db"


class FreshnessResponse(BaseModel):
    """Compare ADO and GitHub HEAD commits; ``fresh`` is true only when both were read and match."""

    project: str
    repo: str
    ado_head_sha: str = ""
    gh_head_sha: str = ""
    fresh: bool = False


class GitHubTokenRequest(BaseModel):
    """Add or replace a GitHub token on a migration profile; requires the manage-settings permission.

    ``token`` carries a secret credential: it is stored for migrations to use and never echoed back,
    appearing as a fixed mask in responses and logs.
    """

    name: str
    token: str = ""
    note: str = ""


class GitHubTokenResponse(BaseModel):
    """Describe a stored GitHub token for the profile token routes.

    ``token`` is always the mask marker, never the stored credential. ``last_validation`` holds the
    scopes, rate-limit headroom and org access seen at ``last_validated_at``.
    """

    id: str
    name: str
    token: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str = ""
    last_validation: dict[str, Any] = Field(default_factory=dict)


class MigrationProfileRequest(BaseModel):
    """Create or update an ADO-to-GitHub migration profile; requires the manage-settings permission.

    ``ado_pat`` carries a secret credential, stored for migrations to use and returned masked in
    every response and log. GitHub tokens are attached through the separate token routes.
    """

    name: str
    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""


class MigrationProfileResponse(BaseModel):
    """Describe a migration profile for the profile routes.

    ``ado_pat`` and the token on every entry of ``github_tokens`` are always masked, never the
    stored credential. ``status`` and ``approval`` carry the governance state: a profile that is not
    active cannot start a run.
    """

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
    """Tell the console where a signed-in user must go next; response of ``GET /v1/onboarding/status``."""

    needs_profile_setup: bool = False
    active_profile_count: int = 0
    default_profile_id: Optional[str] = None
    pending_approval_count: int = 0
    role: str = ""
    blocked_message: Optional[str] = None
    redirect_path: Optional[str] = None
    can_submit_profile: bool = False


class DeleteProfileRequest(BaseModel):
    """Nominate the profile inheriting the default when one is deleted or deactivated; admin only.

    ``new_default_profile_id`` is required only when the profile being removed is the current
    default and ignored otherwise; removing the last active profile is refused outright.
    """

    new_default_profile_id: Optional[str] = None


class DenyProfileRequest(BaseModel):
    """Record why an admin rejected a submitted profile; the reason is audited and shown to the submitter."""

    reason: str = ""


class RegisterBody(BaseModel):
    """Register a platform account that cannot sign in until an administrator approves it.

    ``password`` is a secret: a minimum length is enforced here and it is never returned in a
    response or written to a log.
    """

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""


class ProfileSetupRequest(BaseModel):
    """Set up the first migration profile in one guided onboarding step.

    ``ado_pat`` and ``github_token`` carry secrets, stored for migrations to use and masked in every
    response and log; both are checked against ADO and GitHub before anything is stored.
    """

    name: str
    ado_org_url: str
    ado_pat: str
    gh_org: str
    github_token: str
    github_token_name: str = "Primary"


class MigrationScanRequest(BaseModel):
    """Scan an ADO organization inline, before any profile exists; body of ``POST /v1/migration/scan``.

    ``ado_pat`` carries a secret used only for this scan: it is not stored and never echoed back.
    ``max_repos`` caps the scan for a quick look at a large organization.
    """

    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""
    max_repos: Optional[int] = None


class PhaseRecommendation(BaseModel):
    """Recommend the repositories for one phase; advice only, the binding assignment is made elsewhere."""

    phase: str
    repo_count: int = 0
    risk_min: float = 0.0
    risk_max: float = 0.0
    rationale: str = ""
    repos: list[dict[str, Any]] = Field(default_factory=list)


class MigrationScanResponse(BaseModel):
    """Report an organization scan and its per-phase recommendations.

    ``inventory_gaps`` and ``warnings`` list what the scan could not read, so a plan built on this
    data can be judged against its own coverage.
    """

    scanned_at: str = ""
    projects_scanned: int = 0
    repos_scanned: int = 0
    total_repos: int = 0
    gh_org: str = ""
    recommendations: dict[str, PhaseRecommendation] = Field(default_factory=dict)
    project_details: list[dict[str, Any]] = Field(default_factory=list)
    org_inventory: dict[str, Any] = Field(default_factory=dict)
    pipeline_inventory: dict[str, Any] = Field(default_factory=dict)
    inventory_gaps: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "ok"


# Legacy aliases kept for internal imports
ConnectionProfileRequest = MigrationProfileRequest
ConnectionProfileResponse = MigrationProfileResponse


class ValidateGitHubTokenRequest(BaseModel):
    """Check a GitHub token before a migration is made to depend on it.

    ``token`` carries a secret used only for this check: it is not stored by this call and appears
    in neither the response nor the logs.
    """

    token: str = ""
    gh_org: str = ""


class ValidateGitHubTokenResponse(BaseModel):
    """Report whether a GitHub token can drive a migration; no credential value is ever returned."""

    valid: bool
    message: str = ""
    login: str = ""
    scopes: list[str] = Field(default_factory=list)
    remaining: int = 0
    reset_at: int = 0
    org_accessible: Optional[bool] = None
    warnings: list[str] = Field(default_factory=list)


class ValidateAdoPatRequest(BaseModel):
    """Check an ADO personal access token before a migration depends on it.

    ``ado_pat`` carries a secret used only for this check, never returned and never logged. On the
    profile-scoped route a blank value, or the mask marker the console sends back, falls through to
    the PAT already stored on the profile.
    """

    ado_org_url: str = ""
    ado_pat: str = ""


class ValidateAdoPatResponse(BaseModel):
    """Report whether an ADO PAT can read the source organisation; ``ado_projects`` is the proof that it works."""

    valid: bool
    message: str = ""
    ado_projects: int = 0


class SettingsResponse(BaseModel):
    """Give ``GET /v1/settings`` every migration profile with secrets masked, the active one, and the defaults."""

    active_profile_id: Optional[str] = None
    migration_profiles: list[MigrationProfileResponse] = Field(default_factory=list)
    advanced: dict[str, Any] = Field(default_factory=dict)


class AdvancedSettingsRequest(BaseModel):
    """Change the platform-wide execution defaults; only the fields actually sent are applied.

    ``dry_run_default`` decides whether a console run that states no mode rehearses or migrates for
    real.
    """

    config_path: Optional[str] = None
    db_path: Optional[str] = None
    dry_run_default: Optional[bool] = None
    migration_strategy: Optional[str] = None
    default_phase: Optional[str] = None
    repo_parallel: Optional[int] = None
    pipeline_parallel: Optional[int] = None
    output_dir: Optional[str] = None


class PhaseDefinitionItem(BaseModel):
    """Define one migration phase, its risk ceiling, its repository cap and its place in the sequence."""

    id: str
    name: str
    risk_max: float
    repo_cap: int = 9999
    order: int = 0


class PhaseRemovalItem(BaseModel):
    """Delete a migration phase and reassign the repositories it held.

    ``move_repos_to`` must name a phase that survives the update, so removing a phase never silently
    drops a repository from the plan.
    """

    phase_id: str
    move_repos_to: str


class PhasesUpdateRequest(BaseModel):
    """Replace the phase definitions for the platform or one profile; ``phases`` is the whole new set.

    ``span_to_scan`` also re-spreads the repositories of the latest scan across the new risk bands
    instead of only rewriting the definitions.
    """

    phases: list[PhaseDefinitionItem]
    removals: list[PhaseRemovalItem] = Field(default_factory=list)
    span_to_scan: bool = False
    profile_id: Optional[str] = None


class ValidateConnectionResponse(BaseModel):
    """Report whether a profile reaches both ADO and GitHub; the checks stop at the first failure."""

    valid: bool
    ado_projects: int = 0
    gh_token_remaining: int = 0
    message: str = ""


class PipelineRunStartRequest(BaseModel):
    """Body of ``POST /v1/pipeline/runs``.

    Unknown fields are rejected with 422 rather than dropped (register cross-reference: GAP-004). This
    route used to accept a client-supplied ``agent_live_approved`` boolean that
    disabled the live-execution gate; that field is gone, and a caller still
    sending it — or any other field this model does not declare — now gets a
    loud validation error instead of a silent 200 that ignored it.

    ``dry_run`` is a three-state field: ``True``/``False`` decide, and an omitted
    field defers to the deployment profile's ``dry_run_default``. A preview run
    is the default and a real run needs an explicit opt-in, so that profile
    default must stay reachable (register item GAP-079). The field used to be
    declared ``bool = True``, which made the fallback in
    ``services/accelerator_api/routes/pipeline_routes.py`` unreachable, the same
    bug ``SessionRequest.dry_run`` already had and was fixed for.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = "Manual migration"
    dry_run: Optional[bool] = None
    phase: str = ""
    wave_id: Optional[int] = None
    steps: Optional[list[str]] = None
    repository_id: Optional[str] = None
    migrate_deps_only: bool = True
    override_reason: str = Field(
        default="",
        description="Escalation justification the console collects for a live run; "
                    "carried through to the persisted gate OVERRIDE record.",
    )


class PipelineRunResponse(BaseModel):
    """Wrap one console pipeline run with its per-step state.

    A run returned with status ``awaiting_approval`` has not started; it stays parked until an
    approver releases it.
    """

    run: dict[str, Any]


class PipelineStepDefinition(BaseModel):
    """Describe one selectable console pipeline step; ``id`` is what a run request lists in ``steps``."""

    id: str
    label: str
    description: str


class PhaseAssignmentItem(BaseModel):
    """Move one scanned repository into a migration phase, overriding the phase the scan recommended."""

    project: str
    repo_name: str
    assigned_phase: str


class PhaseAssignmentRequest(BaseModel):
    """Reassign already-scanned repositories to migration phases; requires the manage-settings permission."""

    assignments: list[PhaseAssignmentItem] = Field(default_factory=list)


class DiscoveryRepoItem(BaseModel):
    """Describe one discovered repository, its risk score and its recommended or chosen phase."""

    project: str
    repo_name: str
    total_score: float = 0
    suggested_phase: Optional[str] = None
    assigned_phase: Optional[str] = None
    gh_org: str = ""
    gh_repo: str = ""
    pipeline_count: int = 0


class DiscoveryResponse(BaseModel):
    """Serve the console the last persisted scan for a profile, without contacting ADO.

    ``status`` is ``empty`` when the profile has never been scanned, and ``inventory_gaps`` with
    ``warnings`` record what that scan could not read.
    """

    profile_id: str
    scanned_at: str = ""
    gh_org: str = ""
    repos_scanned: int = 0
    projects_scanned: int = 0
    repos: list[DiscoveryRepoItem] = Field(default_factory=list)
    recommendations: dict[str, Any] = Field(default_factory=dict)
    project_details: list[dict[str, Any]] = Field(default_factory=list)
    org_inventory: dict[str, Any] = Field(default_factory=dict)
    pipeline_inventory_count: int = 0
    inventory_gaps: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "ok"


class LiveApprovalCreateRequest(BaseModel):
    """Ask an approver to authorise a live, non-dry-run execution.

    ``scope_type`` and ``scope_id`` pin the request to exactly one agent session, migrate job or
    pipeline run, so an approval never generalises to other work; a pending row for that scope is
    reused rather than duplicated.
    """

    scope_type: Literal["agent_session", "migrate_job", "pipeline_run"]
    scope_id: str
    profile_id: Optional[str] = None
    reason_request: Optional[str] = None
    context: dict[str, Any] = Field(default_factory=dict)


class LiveApprovalDecisionRequest(BaseModel):
    """Record an approver's justification for releasing or refusing a live run.

    A non-empty ``reason`` is mandatory: approving here is what lets real, irreversible writes to
    GitHub proceed, and it is kept on the approval record as the audit trail.
    """

    reason: str = Field(min_length=1)


class LiveApprovalItem(BaseModel):
    """Describe one live-execution approval; the decision fields are filled in only once an approver rules."""

    id: str
    requester_username: str
    scope_type: str
    scope_id: str
    profile_id: Optional[str] = None
    status: str
    reason_request: Optional[str] = None
    reason_decision: Optional[str] = None
    requested_at: str
    decided_at: Optional[str] = None
    approver_username: Optional[str] = None


class LiveApprovalListResponse(BaseModel):
    """List live-execution approvals by status: the approver's queue of runs that cannot proceed yet."""

    approvals: list[LiveApprovalItem] = Field(default_factory=list)
