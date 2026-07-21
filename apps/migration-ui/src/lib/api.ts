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

export const ACCEL = process.env.NEXT_PUBLIC_ACCELERATOR_URL || 'http://localhost:8080';

export async function fetchHealth(): Promise<{ status: string; version?: string }> {
  const r = await fetch(`${ACCEL}/health`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`Health check failed (${r.status})`);
  return r.json();
}

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

export async function fetchDashboard(): Promise<DashboardSnapshot> {
  return api<DashboardSnapshot>('/v1/dashboard');
}

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

export async function fetchOnboardingStatus(): Promise<OnboardingStatus> {
  return api<OnboardingStatus>('/v1/onboarding/status');
}

export async function fetchSettings(): Promise<UISettings> {
  return api<UISettings>('/v1/settings');
}

export async function fetchMigrationProfile(profileId: string): Promise<MigrationProfile> {
  return api<MigrationProfile>(`/v1/settings/profiles/${profileId}`);
}

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

export async function validateAdoInline(ado_org_url: string, ado_pat: string): Promise<AdoValidationResult> {
  return api<AdoValidationResult>('/v1/settings/validate/ado', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ado_org_url, ado_pat }),
  });
}

export async function validateGitHubInline(token: string, gh_org: string): Promise<TokenValidationResult> {
  return api<TokenValidationResult>('/v1/settings/validate/github', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, gh_org }),
  });
}

export async function runMigrationScan(body: {
  ado_org_url: string;
  ado_pat: string;
  gh_org: string;
  max_repos?: number;
}): Promise<MigrationScanResult> {
  return api<MigrationScanResult>('/v1/migration/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

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

export async function startProfileScan(profileId: string): Promise<ProfileScanJobStatus> {
  return api<ProfileScanJobStatus>(`/v1/settings/profiles/${profileId}/scan`, { method: 'POST' });
}

/** @deprecated Use startProfileScan — scans run in the background; poll fetchProfileScanStatus */
export async function scanMigrationProfile(profileId: string): Promise<MigrationScanResult> {
  await startProfileScan(profileId);
  return fetchProfileScan(profileId);
}

export async function fetchProfileScanStatus(
  profileId: string,
): Promise<ProfileScanJobStatus> {
  return api<ProfileScanJobStatus>(
    `/v1/settings/profiles/${profileId}/scan/status`,
  );
}

export async function fetchProfileScan(profileId: string): Promise<MigrationScanResult> {
  return api<MigrationScanResult>(`/v1/settings/profiles/${profileId}/scan`);
}

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

export async function deleteMigrationProfile(id: string, newDefaultId?: string) {
  return api<{ deleted: string }>(`/v1/settings/profiles/${id}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(newDefaultId ? { new_default_profile_id: newDefaultId } : {}),
  });
}

export async function setProfileDefault(id: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/set-default`, { method: 'POST' });
}

export async function deactivateProfile(id: string, newDefaultId?: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/deactivate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(newDefaultId ? { new_default_profile_id: newDefaultId } : {}),
  });
}

export async function fetchPendingProfiles() {
  return api<MigrationProfile[]>('/v1/settings/profiles/pending');
}

export async function fetchMyPendingProfiles() {
  return api<MigrationProfile[]>('/v1/settings/profiles/mine/pending');
}

export async function approveProfile(id: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/approve`, { method: 'POST' });
}

export async function denyProfile(id: string, reason = '') {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/deny`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

export async function appealProfile(id: string) {
  return api<MigrationProfile>(`/v1/settings/profiles/${id}/appeal`, { method: 'POST' });
}

export async function activateMigrationProfile(id: string) {
  return api<{ active_profile_id: string }>(`/v1/settings/profiles/${id}/activate`, {
    method: 'POST',
  });
}

export async function validateProfileSource(profileId: string): Promise<AdoValidationResult> {
  return api<AdoValidationResult>(`/v1/settings/profiles/${profileId}/validate/source`, {
    method: 'POST',
  });
}

export async function validateMigrationProfile(profileId: string) {
  return api<{ valid: boolean; message: string; ado_projects: number; gh_token_remaining: number }>(
    `/v1/settings/profiles/${profileId}/validate`,
    { method: 'POST' },
  );
}

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

export async function validateConnection() {
  return api<{ valid: boolean; message: string; ado_projects: number; gh_token_remaining: number }>(
    '/v1/settings/validate',
    { method: 'POST' },
  );
}

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

export async function deleteGitHubToken(profileId: string, tokenId: string) {
  return api<{ deleted: string }>(`/v1/settings/profiles/${profileId}/tokens/${tokenId}`, {
    method: 'DELETE',
  });
}

export async function validateGitHubTokenSaved(
  profileId: string,
  tokenId: string,
): Promise<TokenValidationResult> {
  return api<TokenValidationResult>(
    `/v1/settings/profiles/${profileId}/tokens/${tokenId}/validate`,
    { method: 'POST' },
  );
}

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

export async function updateAdvanced(data: Partial<AdvancedSettings>) {
  return api<AdvancedSettings>('/v1/settings/advanced', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

export async function fetchPipelineSteps(context: 'migrate' | 'full' = 'migrate'): Promise<StepDefinition[]> {
  return api<StepDefinition[]>(`/v1/pipeline/steps?context=${context}`);
}

export async function fetchDiscovery(profileId: string): Promise<DiscoverySnapshot> {
  return api<DiscoverySnapshot>(`/v1/settings/profiles/${profileId}/discovery`);
}

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

export async function fetchPipelineRun(id: string): Promise<{ run: PipelineRun }> {
  return api<{ run: PipelineRun }>(`/v1/pipeline/runs/${id}`);
}

export async function startPipelineRun(body: {
  name: string;
  dry_run: boolean;
  phase: string;
  wave_id?: number | null;
  steps?: string[];
  repository_id?: string | null;
  migrate_deps_only?: boolean;
}) {
  return api<{ run: PipelineRun }>('/v1/pipeline/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

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

export async function fetchHistory(
  params: AuditHistoryParams = {},
): Promise<AuditHistoryResponse> {
  return api<AuditHistoryResponse>(`/v1/history/sessions${historyQueryString(params)}`);
}

export async function fetchAuditEventTypes(profileId?: string): Promise<string[]> {
  const q = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
  const data = await api<{ event_types: string[] }>(`/v1/history/event-types${q}`);
  return data.event_types;
}

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

export type LiveApprovalItem = {
  id: string;
  requester_username: string;
  scope_type: string;
  scope_id: string;
  profile_id?: string | null;
  assignment_id?: string | null;
  status: string;
  reason_request?: string | null;
  reason_decision?: string | null;
  requested_at: string;
  decided_at?: string | null;
  approver_username?: string | null;
};

export async function fetchLiveApprovals(status = 'pending') {
  return api<{ approvals: LiveApprovalItem[] }>(
    `/v1/platform/approvals?status=${encodeURIComponent(status)}`,
  );
}

export async function approveLiveExecution(approvalId: string, reason: string) {
  return api<LiveApprovalItem>(`/v1/platform/approvals/${approvalId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

export async function denyLiveExecution(approvalId: string, reason: string) {
  return api<LiveApprovalItem>(`/v1/platform/approvals/${approvalId}/deny`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

export { ACCEL as ACCELERATOR_URL };
