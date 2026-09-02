export type StepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'warn'
  | 'failed'
  | 'skipped'
  | 'dry_run_complete'
  | 'awaiting_approval';

export interface PipelineStep {
  id: string;
  label: string;
  description: string;
  status: StepStatus;
  message?: string;
  started_at?: string;
  completed_at?: string;
  result?: Record<string, unknown>;
}

export interface PipelineRun {
  id: string;
  name: string;
  status: string;
  dry_run: boolean;
  phase: string;
  wave_id?: number | null;
  repository_id?: string | null;
  migrate_deps_only?: boolean;
  steps: PipelineStep[];
  logs: string[];
  error?: string | null;
  created_at: string;
  updated_at: string;
  started_by_user_id?: string | null;
  started_by_username?: string | null;
  started_by_display_name?: string | null;
  started_by_label?: string;
  approved_by_username?: string | null;
  approved_by_display_name?: string | null;
  approved_by_label?: string;
  live_approval_status?: string | null;
  current_step?: string;
}

export interface GitHubTokenEntry {
  id: string;
  name: string;
  token: string;
  note: string;
  created_at?: string;
  updated_at?: string;
  last_validated_at?: string;
  last_validation?: TokenValidationResult;
}

/** Source ADO org + target GitHub org + tokens for one migration path. */
export interface MigrationProfile {
  id: string;
  name: string;
  ado_org_url: string;
  ado_pat: string;
  gh_org: string;
  github_tokens: GitHubTokenEntry[];
  status?: string;
  is_default?: boolean;
  submitted_by?: string;
  approval?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  last_scan_at?: string;
  scan_summary?: ScanSummary;
}

export interface OnboardingStatus {
  needs_profile_setup: boolean;
  active_profile_count: number;
  default_profile_id: string | null;
  pending_approval_count: number;
  role: string;
  blocked_message: string | null;
  redirect_path: string | null;
  can_submit_profile: boolean;
}

export interface PhaseRecommendation {
  phase: string;
  phase_name?: string;
  repo_count: number;
  risk_min: number;
  risk_max: number;
  risk_band_max?: number;
  rationale: string;
  repos?: ScanRepoEntry[];
}

export interface ScanRepoEntry {
  project: string;
  repo_name: string;
  total_score: number;
  assigned_phase?: string;
  gh_org?: string;
  gh_repo?: string;
  pipeline_count?: number;
}

export interface MigrationScanResult {
  scanned_at: string;
  projects_scanned: number;
  repos_scanned: number;
  total_repos: number;
  gh_org: string;
  recommendations: Record<string, PhaseRecommendation>;
  project_details?: ScanProjectDetail[];
  org_inventory?: Record<string, number>;
  pipeline_inventory?: Record<string, unknown>;
  inventory_gaps?: Array<Record<string, string>>;
  warnings?: string[];
  status?: string;
}

export interface ScanProjectDetail {
  project: string;
  project_id?: string;
  repo_count: number;
  disabled_count?: number;
  pipeline_count?: number;
  service_connection_count?: number;
  service_connections?: Array<{ name: string; type?: string; id?: string; is_ready?: boolean }>;
  variable_group_count?: number;
  variable_groups?: Array<{ name: string; id?: string; variable_count?: number; is_shared?: boolean }>;
  environment_count?: number;
  environments?: string[];
  artifact_feed_count?: number;
  artifact_feeds?: Array<{ name: string; id?: string; is_public?: boolean }>;
  team_count?: number;
  teams?: string[];
  iteration_count?: number;
  work_item_count?: number;
  work_item_types?: string[];
  work_item_type_count?: number;
  test_plan_count?: number;
  test_plans?: Array<{ name: string; id?: string | number; state?: string }>;
  error?: string | null;
}

export interface DiscoveryRepoItem {
  project: string;
  repo_name: string;
  total_score: number;
  suggested_phase?: string | null;
  assigned_phase?: string | null;
  gh_org?: string;
  gh_repo?: string;
  pipeline_count?: number;
}

export interface DiscoverySnapshot {
  profile_id: string;
  scanned_at: string;
  gh_org: string;
  repos_scanned: number;
  projects_scanned?: number;
  repos: DiscoveryRepoItem[];
  recommendations: Record<string, PhaseRecommendation>;
  project_details?: ScanProjectDetail[];
  org_inventory?: Record<string, number>;
  pipeline_inventory_count?: number;
  inventory_gaps?: Array<Record<string, string>>;
  warnings?: string[];
  status?: string;
}

export interface ValidationCheckRow {
  check: string;
  verdict: string;
  detail: string;
}

export interface ValidationRepoResult {
  project: string;
  repo: string;
  gh_target?: string;
  overall: string;
  primary_reason?: string;
  message?: string;
  detail?: string;
  checks?: ValidationCheckRow[];
}

export interface ValidationResult {
  total: number;
  matched: number;
  failed: number;
  results: ValidationRepoResult[];
}

export interface ScanSummary {
  projects_scanned: number;
  repos_scanned: number;
  recommendations: Record<string, {
    repo_count: number;
    risk_min: number;
    risk_max: number;
    rationale: string;
  }>;
}

export interface TokenValidationResult {
  valid: boolean;
  message: string;
  login?: string;
  scopes?: string[];
  remaining?: number;
  warnings?: string[];
}

export interface AdoValidationResult {
  valid: boolean;
  message: string;
  ado_projects: number;
}

export interface AdvancedSettings {
  config_path: string;
  db_path: string;
  dry_run_default: boolean;
  migration_strategy: string;
  default_phase: string;
  repo_parallel: number;
  pipeline_parallel: number;
  output_dir: string;
  phases?: PhaseDefinition[];
}

export interface PhaseDefinition {
  id: string;
  name: string;
  risk_max: number;
  risk_min?: number;
  repo_cap: number;
  order: number;
  repo_count?: number;
}

export interface PhaseCoverage {
  spans_0_100: boolean;
  covers_scan_min: boolean;
  covers_scan_max: boolean;
  scan_score_min: number | null;
  scan_score_max: number | null;
  errors: string[];
  gaps: string[];
}

export interface PhasesPayload {
  phases: PhaseDefinition[];
  coverage: PhaseCoverage;
  repo_counts_by_phase: Record<string, number>;
  default_phase: string;
  rescan?: {
    profile_id?: string;
    repos_scanned?: number;
    projects_scanned?: number;
    scanned_at?: string;
    synced?: number;
    skipped?: boolean;
    reason?: string;
    status?: 'started' | 'already_running';
    running?: boolean;
  };
}

export interface PhaseRemoval {
  phase_id: string;
  move_repos_to: string;
}

export interface UISettings {
  active_profile_id: string | null;
  migration_profiles: MigrationProfile[];
  advanced: AdvancedSettings;
}

export interface StepDefinition {
  id: string;
  label: string;
  description: string;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  profile_id: string;
  actor: string;
  payload_json?: string;
  created_at: string;
}

export interface AuditHistoryResponse {
  sessions: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  count: number;
}

export interface AuditHistoryParams {
  profileId?: string;
  limit?: number;
  offset?: number;
  actor?: string;
  eventType?: string;
  search?: string;
  dateFrom?: string;
  dateTo?: string;
}

export interface PhaseGate {
  phase: string;
  status: string;
  repo_success_pct: number;
}

export interface ActiveMigration {
  id: string;
  name: string;
  status: string;
  dry_run: boolean;
  phase: string;
  wave_id?: number | null;
  repository_id?: string | null;
  current_step: string;
  started_by_username: string;
  started_by_display_name: string;
  created_at: string;
  updated_at: string;
}

export interface DashboardSnapshot {
  total_repos: number;
  completed_repos: number;
  failed_repos: number;
  total_pipelines: number;
  inventory_count: number;
  phase_gates: PhaseGate[];
  active_migrations?: ActiveMigration[];
}

export interface PipelineReadinessItem {
  project: string;
  pipeline_id: string | number;
  pipeline_name: string;
  repo_name?: string;
  pipeline_type?: string;
  classification: string;
  conversion?: string;
  migration_status?: string;
  workflow_file?: string;
  effort_hours: number;
  service_connections: string | number;
  blockers?: string[];
  warnings?: string[];
}

export interface ReadinessSnapshot {
  auto: number;
  assisted: number;
  manual: number;
  total_pipelines?: number;
  total_effort_hours: number;
  inventory_refreshed?: boolean;
  pipelines: PipelineReadinessItem[];
}
