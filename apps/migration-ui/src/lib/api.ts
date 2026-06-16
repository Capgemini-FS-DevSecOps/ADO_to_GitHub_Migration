import type {
  AdvancedSettings,
  AdoValidationResult,
  DashboardSnapshot,
  GitHubTokenEntry,
  MigrationProfile,
  MigrationScanResult,
  PipelineRun,
  ReadinessSnapshot,
  StepDefinition,
  DiscoverySnapshot,
  ValidationResult,
  TokenValidationResult,
  UISettings,
  PhaseDefinition,
  PhaseRemoval,
  PhasesPayload,
} from './types';

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
      `Cannot reach Accelerator API at ${ACCEL}. If using Docker, ensure the accelerator container is running on port 8080. Otherwise start with .\\scripts\\run-local.ps1 or docker compose up.`,
    );
  }
  if (!r.ok) {
    const err = await r.text();
    throw new Error(err || `API ${path} failed (${r.status})`);
  }
  return r.json();
}

export async function fetchDashboard(): Promise<DashboardSnapshot> {
  return api<DashboardSnapshot>('/v1/dashboard');
}

export async function fetchReadiness(): Promise<ReadinessSnapshot> {
  return api<ReadinessSnapshot>('/v1/pipeline-readiness', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config_path: 'migration.yaml' }),
  });
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

export async function scanMigrationProfile(profileId: string): Promise<MigrationScanResult> {
  return api<MigrationScanResult>(`/v1/settings/profiles/${profileId}/scan`, { method: 'POST' });
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

export async function deleteMigrationProfile(id: string) {
  return api<{ deleted: string }>(`/v1/settings/profiles/${id}`, { method: 'DELETE' });
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

export async function fetchPhases(profileId?: string): Promise<PhasesPayload> {
  const q = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
  return api<PhasesPayload>(`/v1/settings/phases${q}`);
}

export async function updatePhases(body: {
  phases: PhaseDefinition[];
  removals?: PhaseRemoval[];
  span_to_scan?: boolean;
  profile_id?: string;
}): Promise<PhasesPayload> {
  return api<PhasesPayload>('/v1/settings/phases', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchPipelineSteps(context: 'migrate' | 'full' = 'migrate'): Promise<StepDefinition[]> {
  return api<StepDefinition[]>(`/v1/pipeline/steps?context=${context}`);
}

export async function fetchDiscovery(profileId: string): Promise<DiscoverySnapshot> {
  return api<DiscoverySnapshot>(`/v1/settings/profiles/${profileId}/discovery`);
}

export async function savePhaseAssignments(
  profileId: string,
  assignments: { project: string; repo_name: string; assigned_phase: string }[],
) {
  return api<{ updated: number }>(`/v1/settings/profiles/${profileId}/phase-assignments`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments }),
  });
}

export async function runValidation(body: {
  config_path: string;
  db_path?: string;
  input_path?: string;
}): Promise<ValidationResult> {
  return api<ValidationResult>('/v1/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchPipelineRuns(): Promise<{ runs: PipelineRun[] }> {
  return api<{ runs: PipelineRun[] }>('/v1/pipeline/runs');
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

export async function listAssignments(profileId: string) {
  const data = await api<{ assignments: Array<Record<string, unknown>> }>(
    `/v1/profiles/${profileId}/assignments`,
  );
  return data.assignments;
}

export async function fetchHistory(profileId?: string) {
  const q = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
  const data = await api<{ sessions: Array<Record<string, unknown>> }>(
    `/v1/history/sessions${q}`,
  );
  return data.sessions;
}

export async function fetchAssignmentGate(assignmentId: string) {
  return api<Record<string, unknown>>(`/v1/assignments/${assignmentId}/gate-status`);
}

export { ACCEL as ACCELERATOR_URL };
