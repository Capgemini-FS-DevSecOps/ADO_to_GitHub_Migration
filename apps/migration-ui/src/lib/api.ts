import type {
  AdvancedSettings,
  AdoValidationResult,
  AuditHistoryParams,
  AuditHistoryResponse,
  DashboardSnapshot,
  GitHubTokenEntry,
  MigrationProfile,
  OnboardingStatus,
  MigrationScanResult,
  PipelineRun,
  ReadinessSnapshot,
  StepDefinition,
  DiscoverySnapshot,
  ValidationResult,
  TokenValidationResult,
  UISettings,
} from './types';

/** Base URL of the accelerator API, from NEXT_PUBLIC_ACCELERATOR_URL or localhost:8080. */
export const ACCEL = process.env.NEXT_PUBLIC_ACCELERATOR_URL || 'http://localhost:8080';

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${ACCEL}${path}`, {
      cache: 'no-store',
      credentials: 'include',
      ...init,
    });
  } catch {
    throw new Error(
      `Cannot reach Accelerator API at ${ACCEL}. If using Docker, ensure the accelerator container is running on port 8080. Otherwise start with .\\scripts\\dev\\run-local-agent.ps1 and .\\scripts\\dev\\run-ui.ps1.`,
    );
  }
    if (!r.ok) {
    if (r.status === 401) {
      throw new Error('Not authenticated — sign in at /login');
    }
    const err = await r.text();
    throw new Error(err || `API ${path} failed (${r.status})`);
  }
  return r.json();
}

/** Fetch the dashboard snapshot from the accelerator (GET /v1/dashboard). */
export async function fetchDashboard(): Promise<DashboardSnapshot> {
  return api<DashboardSnapshot>('/v1/dashboard');
}

/**
 * Fetch the pipeline readiness snapshot from the accelerator
 * (POST /v1/pipeline-readiness), optionally re-scanning the pipeline inventory first.
 */
export async function fetchReadiness(options?: {
  refreshInventory?: boolean;
}): Promise<ReadinessSnapshot> {
  return api<ReadinessSnapshot>('/v1/pipeline-readiness', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      config_path: 'migration.yaml',
      refresh_inventory: options?.refreshInventory ?? false,
    }),
  });
}

/** Fetch onboarding progress for the current user (GET /v1/onboarding/status). */
export async function fetchOnboardingStatus(): Promise<OnboardingStatus> {
  return api<OnboardingStatus>('/v1/onboarding/status');
}

/** Fetch the console settings bundle from the accelerator (GET /v1/settings). */
export async function fetchSettings(): Promise<UISettings> {
  return api<UISettings>('/v1/settings');
}

/** Fetch a single migration profile by id (GET /v1/settings/profiles/{id}). */
export async function fetchMigrationProfile(profileId: string): Promise<MigrationProfile> {
  return api<MigrationProfile>(`/v1/settings/profiles/${profileId}`);
}

/**
 * Create a migration profile with its ADO and GitHub credentials in one step
 * (POST /v1/settings/profiles/setup) and return the created profile.
 */
export async function setupMigrationProfile(data: {
  name: string;
  ado_org_url: string;
  ado_pat: string;
  gh_org: string;
  github_token: string;
  github_token_name?: string;
}) {
  return api<MigrationProfile>('/v1/settings/profiles/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

/**
 * Validate unsaved ADO organisation credentials before a profile exists
 * (POST /v1/settings/validate/ado).
 */
export async function validateAdoInline(ado_org_url: string, ado_pat: string): Promise<AdoValidationResult> {
  return api<AdoValidationResult>('/v1/settings/validate/ado', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ado_org_url, ado_pat }),
  });
}

/**
 * Validate an unsaved GitHub token against an organisation before a profile exists
 * (POST /v1/settings/validate/github).
 */
export async function validateGitHubInline(token: string, gh_org: string): Promise<TokenValidationResult> {
  return api<TokenValidationResult>('/v1/settings/validate/github', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, gh_org }),
  });
}

/** Status of the background discovery scan for a profile, as reported by the accelerator. */
export type ProfileScanJobStatus = {
  profile_id: string;
  running: boolean;
  status?: string;
  error?: string | null;
  scanned_at?: string | null;
  repos_scanned?: number | null;
  projects_scanned?: number | null;
  service_connections?: number | null;
};

/** Start a background discovery scan for a profile (POST /v1/settings/profiles/{id}/scan). */
export async function startProfileScan(profileId: string): Promise<ProfileScanJobStatus> {
  return api<ProfileScanJobStatus>(`/v1/settings/profiles/${profileId}/scan`, { method: 'POST' });
}

/** @deprecated Use startProfileScan — scans run in the background; poll fetchProfileScanStatus */
export async function scanMigrationProfile(profileId: string): Promise<MigrationScanResult> {
  await startProfileScan(profileId);
  return fetchProfileScan(profileId);
}

/** Poll the state of a profile's background scan (GET /v1/settings/profiles/{id}/scan/status). */
export async function fetchProfileScanStatus(
  profileId: string,
): Promise<ProfileScanJobStatus> {
  return api<ProfileScanJobStatus>(
    `/v1/settings/profiles/${profileId}/scan/status`,
  );
}

/** Fetch the stored scan results for a profile (GET /v1/settings/profiles/{id}/scan). */
export async function fetchProfileScan(profileId: string): Promise<MigrationScanResult> {
  return api<MigrationScanResult>(`/v1/settings/profiles/${profileId}/scan`);
}

/**
 * Create or update a migration profile — POST /v1/settings/profiles when no id is given,
 * PUT /v1/settings/profiles/{id} otherwise. Returns the saved profile.
 */
export async function saveMigrationProfile(
  data: Partial<MigrationProfile> & { name: string },
  id?: string,
) {
  const path = id ? `/v1/settings/profiles/${id}` : '/v1/settings/profiles';
  return api<MigrationProfile>(path, {
    method: id ? 'PUT' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

/**
 * Delete a migration profile (DELETE /v1/settings/profiles/{id}), optionally naming
 * the profile that takes over as default.
 */
export async function deleteMigrationProfile(id: string, newDefaultId?: string) {
  return api<{ deleted: string }>(`/v1/settings/profiles/${id}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(newDefaultId ? { new_default_profile_id: newDefaultId } : {}),
  });
}

/** Mark a profile as the default one (POST /v1/settings/profiles/{id}/set-default). */
export async function setProfileDefault(id: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/set-default`, { method: 'POST' });
}

/** Fetch profiles awaiting an approval decision (GET /v1/settings/profiles/pending). */
export async function fetchPendingProfiles() {
  return api<MigrationProfile[]>('/v1/settings/profiles/pending');
}

/** Fetch the current user's own profiles awaiting approval (GET /v1/settings/profiles/mine/pending). */
export async function fetchMyPendingProfiles() {
  return api<MigrationProfile[]>('/v1/settings/profiles/mine/pending');
}

/** Approve a pending migration profile (POST /v1/settings/profiles/{id}/approve). */
export async function approveProfile(id: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/approve`, { method: 'POST' });
}

/** Deny a pending migration profile with an optional reason (POST /v1/settings/profiles/{id}/deny); resolves to the now-denied profile. */
export async function denyProfile(id: string, reason: string = '') {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/deny`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

/** Appeal a denied migration profile to send it back for review (POST /v1/settings/profiles/{id}/appeal). */
export async function appealProfile(id: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/appeal`, { method: 'POST' });
}

/**
 * Switch the session to a different migration profile
 * (POST /v1/settings/profiles/{id}/activate) and return the newly active profile id.
 */
export async function activateMigrationProfile(id: string) {
  return api<{ active_profile_id: string }>(`/v1/settings/profiles/${id}/activate`, {
    method: 'POST',
  });
}

/**
 * Validate a profile's stored ADO source credentials
 * (POST /v1/settings/profiles/{id}/validate/source).
 */
export async function validateProfileSource(profileId: string): Promise<AdoValidationResult> {
  return api<AdoValidationResult>(`/v1/settings/profiles/${profileId}/validate/source`, {
    method: 'POST',
  });
}

/**
 * Validate both ends of a saved profile (POST /v1/settings/profiles/{id}/validate) and return
 * the reachable ADO project count and remaining GitHub rate limit.
 */
export async function validateMigrationProfile(profileId: string) {
  return api<{ valid: boolean; message: string; ado_projects: number; gh_token_remaining: number }>(
    `/v1/settings/profiles/${profileId}/validate`,
    { method: 'POST' },
  );
}

/**
 * Validate replacement ADO credentials against an existing profile without saving them
 * (POST /v1/settings/profiles/{id}/validate/ado).
 */
export async function validateAdoForProfile(
  profileId: string,
  ado_org_url: string,
  ado_pat: string,
): Promise<AdoValidationResult> {
  return api<AdoValidationResult>(`/v1/settings/profiles/${profileId}/validate/ado`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ado_org_url, ado_pat }),
  });
}

/**
 * Add or update a GitHub token on a profile — POST /v1/settings/profiles/{id}/tokens when no
 * token id is given, PUT /v1/settings/profiles/{id}/tokens/{tokenId} otherwise.
 */
export async function saveGitHubToken(
  profileId: string,
  data: Partial<GitHubTokenEntry> & { name: string },
  tokenId?: string,
) {
  const path = tokenId
    ? `/v1/settings/profiles/${profileId}/tokens/${tokenId}`
    : `/v1/settings/profiles/${profileId}/tokens`;
  return api<GitHubTokenEntry>(path, {
    method: tokenId ? 'PUT' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

/** Remove a GitHub token from a profile (DELETE /v1/settings/profiles/{id}/tokens/{tokenId}). */
export async function deleteGitHubToken(profileId: string, tokenId: string) {
  return api<{ deleted: string }>(`/v1/settings/profiles/${profileId}/tokens/${tokenId}`, {
    method: 'DELETE',
  });
}

/**
 * Validate a token already stored on a profile
 * (POST /v1/settings/profiles/{id}/tokens/{tokenId}/validate).
 */
export async function validateGitHubTokenSaved(
  profileId: string,
  tokenId: string,
): Promise<TokenValidationResult> {
  return api<TokenValidationResult>(
    `/v1/settings/profiles/${profileId}/tokens/${tokenId}/validate`,
    { method: 'POST' },
  );
}

/**
 * Validate a GitHub token against a profile before saving it
 * (POST /v1/settings/profiles/{id}/tokens/validate).
 */
export async function validateGitHubTokenInline(
  profileId: string,
  token: string,
): Promise<TokenValidationResult> {
  return api<TokenValidationResult>(`/v1/settings/profiles/${profileId}/tokens/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
}

/** Update advanced migration settings (PUT /v1/settings/advanced) and return the saved values. */
export async function updateAdvanced(data: Partial<AdvancedSettings>) {
  return api<AdvancedSettings>('/v1/settings/advanced', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

/** Fetch the pipeline step definitions for a context (GET /v1/pipeline/steps). */
export async function fetchPipelineSteps(context: 'migrate' | 'full' = 'migrate'): Promise<StepDefinition[]> {
  return api<StepDefinition[]>(`/v1/pipeline/steps?context=${context}`);
}

/** Fetch the discovery snapshot for a profile (GET /v1/settings/profiles/{id}/discovery). */
export async function fetchDiscovery(profileId: string): Promise<DiscoverySnapshot> {
  return api<DiscoverySnapshot>(`/v1/settings/profiles/${profileId}/discovery`);
}

/**
 * Run commit-level source-versus-target validation (POST /v1/validate) for a profile, phase or
 * supplied config, and return the validation result.
 */
export async function runValidation(body: {
  profile_id?: string;
  phase?: string;
  config_path?: string;
  config_yaml?: string;
  db_path?: string;
  input_path?: string;
  input_text?: string;
}): Promise<ValidationResult> {
  return api<ValidationResult>('/v1/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Fetch a page of pipeline runs (GET /v1/pipeline/runs) with paging metadata and a status
 * summary. Defaults to the first 20 runs.
 */
export async function fetchPipelineRuns(params?: {
  limit?: number;
  offset?: number;
}): Promise<{
  runs: PipelineRun[];
  total: number;
  limit: number;
  offset: number;
  summary?: {
    total: number;
    completed_live: number;
    dry_run: number;
    active: number;
    awaiting_approval?: number;
    failed: number;
  };
}> {
  const limit = params?.limit ?? 20;
  const offset = params?.offset ?? 0;
  return api<{
    runs: PipelineRun[];
    total: number;
    limit: number;
    offset: number;
    summary?: {
      total: number;
      completed_live: number;
      dry_run: number;
      active: number;
      awaiting_approval?: number;
      failed: number;
    };
  }>(
    `/v1/pipeline/runs?limit=${limit}&offset=${offset}`,
  );
}

/** Fetch a single pipeline run by id (GET /v1/pipeline/runs/{id}). */
export async function fetchPipelineRun(id: string): Promise<{ run: PipelineRun }> {
  return api<{ run: PipelineRun }>(`/v1/pipeline/runs/${id}`);
}

/**
 * Start a pipeline run (POST /v1/pipeline/runs) and return the created run. The accelerator
 * rejects any field not listed here.
 */
export async function startPipelineRun(body: {
  name: string;
  dry_run: boolean;
  phase: string;
  wave_id?: number | null;
  steps?: string[];
  repository_id?: string | null;
  migrate_deps_only?: boolean;
  override_reason?: string;
}) {
  return api<{ run: PipelineRun }>('/v1/pipeline/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Cancel an in-flight pipeline run (POST /v1/pipeline/runs/{id}/cancel). */
export async function cancelPipelineRun(runId: string) {
  return api<{ run: PipelineRun; cancelled: boolean }>(`/v1/pipeline/runs/${runId}/cancel`, {
    method: 'POST',
  });
}

function historyQueryString(params: AuditHistoryParams): string {
  const q = new URLSearchParams();
  if (params.profileId) q.set('profile_id', params.profileId);
  if (params.limit != null) q.set('limit', String(params.limit));
  if (params.offset != null) q.set('offset', String(params.offset));
  if (params.actor) q.set('actor', params.actor);
  if (params.eventType) q.set('event_type', params.eventType);
  if (params.search) q.set('search', params.search);
  if (params.dateFrom) q.set('date_from', params.dateFrom);
  if (params.dateTo) q.set('date_to', params.dateTo);
  const s = q.toString();
  return s ? `?${s}` : '';
}

/** Fetch a filtered page of audit history sessions (GET /v1/history/sessions). */
export async function fetchHistory(
  params: AuditHistoryParams = {},
): Promise<AuditHistoryResponse> {
  return api<AuditHistoryResponse>(`/v1/history/sessions${historyQueryString(params)}`);
}

/**
 * Fetch the distinct audit event types available for filtering (GET /v1/history/event-types),
 * optionally narrowed to one profile.
 */
export async function fetchAuditEventTypes(profileId?: string): Promise<string[]> {
  const q = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
  const data = await api<{ event_types: string[] }>(`/v1/history/event-types${q}`);
  return data.event_types;
}

/**
 * Download the filtered audit history as a CSV file (GET /v1/history/sessions/export) and
 * save it in the browser as audit-history.csv.
 */
export async function downloadAuditHistoryExport(
  params: AuditHistoryParams = {},
): Promise<void> {
  const r = await fetch(`${ACCEL}/v1/history/sessions/export${historyQueryString(params)}`, {
    credentials: 'include',
    cache: 'no-store',
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(err || `Export failed (${r.status})`);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'audit-history.csv';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** A request to run a migration scope live, with its requester, status and decision details. */
export type LiveApprovalItem = {
  id: string;
  requester_username: string;
  scope_type: string;
  scope_id: string;
  profile_id?: string | null;
  status: string;
  reason_request?: string | null;
  reason_decision?: string | null;
  requested_at: string;
  decided_at?: string | null;
  approver_username?: string | null;
};

/** Fetch live-execution approval requests by status (GET /v1/platform/approvals), pending by default. */
export async function fetchLiveApprovals(status: string = 'pending') {
  return api<{ approvals: LiveApprovalItem[] }>(
    `/v1/platform/approvals?status=${encodeURIComponent(status)}`,
  );
}

/** Approve a live-execution request with a reason (POST /v1/platform/approvals/{id}/approve). */
export async function approveLiveExecution(approvalId: string, reason: string) {
  return api<LiveApprovalItem>(`/v1/platform/approvals/${approvalId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

/** Deny a live-execution request with a reason (POST /v1/platform/approvals/{id}/deny). */
export async function denyLiveExecution(approvalId: string, reason: string) {
  return api<LiveApprovalItem>(`/v1/platform/approvals/${approvalId}/deny`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

/** Alias of ACCEL for callers that prefer the fuller name. */
export { ACCEL as ACCELERATOR_URL };
