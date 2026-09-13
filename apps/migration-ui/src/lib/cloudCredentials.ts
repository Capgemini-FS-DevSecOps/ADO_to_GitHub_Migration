import { ACCEL } from './api';

export type CloudCredentialSource = {
  provider: string;
  service: string;
  status: string;
  completeness: string;
  primary_method?: string | null;
  alternate_methods: string[];
  region?: string | null;
  project?: string | null;
  endpoint?: string | null;
  missing_fields: string[];
  admin_supplied_fields: Record<string, string>;
  last_scan_at?: string | null;
  last_probe_status?: string | null;
  last_probe_category?: string | null;
  last_probe_message?: string | null;
  approved_at?: string | null;
  approved_by?: string | null;
};

export type CloudCredentialsResponse = {
  last_full_scan_at?: string | null;
  sources: CloudCredentialSource[];
  platform_model?: {
    provider: string;
    model_id: string;
    region?: string | null;
    endpoint?: string | null;
    read_only: boolean;
    synced_model_id: string;
    available: boolean;
  } | null;
};

async function cloudApi<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${ACCEL}${path}`, {
      cache: 'no-store',
      credentials: 'include',
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new Error(
      `Cannot reach Accelerator API at ${ACCEL}. Use http://localhost:3000 (not 127.0.0.1) or add your UI origin to CORS_ORIGINS, then sign in again.`,
    );
  }
  if (!r.ok) {
    if (r.status === 401) {
      throw new Error('Not authenticated — sign in at /login, then reopen Cloud credentials.');
    }
    if (r.status === 403) {
      throw new Error('Cloud credentials require an admin account (`can_manage_models`).');
    }
    if (r.status === 404) {
      throw new Error(
        `Cloud credentials API not found at ${ACCEL}. Another process is likely bound to port 8080 ` +
          'Stop the local Accelerator window, or use only ' +
          'docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build — not both.',
      );
    }
    const text = await r.text();
    throw new Error(text || `Cloud credentials API failed (${r.status})`);
  }
  return r.json() as Promise<T>;
}

/**
 * Read the cloud credential sources from `GET /v1/settings/cloud-credentials`, asking the
 * accelerator to re-probe each provider first unless scanning is disabled. Returns the source list
 * plus the platform model summary.
 */
export async function fetchCloudCredentials(scan: boolean = true): Promise<CloudCredentialsResponse> {
  return cloudApi<CloudCredentialsResponse>(
    `/v1/settings/cloud-credentials${scan ? '?scan=true' : ''}`,
  );
}

/**
 * Re-probe every provider via `POST /v1/settings/cloud-credentials/scan`.
 * Returns the refreshed source list and platform model summary.
 */
export async function rescanCloudCredentials(): Promise<CloudCredentialsResponse> {
  return cloudApi<CloudCredentialsResponse>('/v1/settings/cloud-credentials/scan', { method: 'POST' });
}

/**
 * Update the admin-supplied fields of one provider via
 * `PATCH /v1/settings/cloud-credentials/{provider}`. Returns the updated source.
 */
export async function patchCloudCredential(
  provider: string,
  body: Record<string, string>,
): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

/**
 * Whether an armed credential decision may be committed.
 *
 * CA-002: approving a source lets agents authenticate to a cloud provider and revoking one
 * cuts running models off, so both take a second, deliberate click. Only `reject` records a
 * reason server-side (`POST .../reject` reads `body.reason`), so only `reject` demands one —
 * asking for a justification the API drops would be theatre, not an audit trail.
 */
export function credentialDecisionReady(
  action: 'approve' | 'reject' | 'revoke',
  reason: string,
): boolean {
  return action !== 'reject' || reason.trim().length > 0;
}

/**
 * Approve a provider's credentials via `POST /v1/settings/cloud-credentials/{provider}/approve`.
 * Returns the updated source.
 */
export async function approveCloudCredential(provider: string): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}/approve`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

/**
 * Reject a provider's credentials via `POST /v1/settings/cloud-credentials/{provider}/reject`,
 * recording the operator's reason. Returns the updated source.
 */
export async function rejectCloudCredential(
  provider: string,
  reason: string = '',
): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

/**
 * Revoke a previous approval via `POST /v1/settings/cloud-credentials/{provider}/revoke`.
 * Returns the updated source.
 */
export async function revokeCloudCredential(provider: string): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}/revoke`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
