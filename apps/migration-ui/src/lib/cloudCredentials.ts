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

export async function fetchCloudCredentials(scan = true): Promise<CloudCredentialsResponse> {
  const q = scan ? '?scan=true' : '';
  return cloudApi<CloudCredentialsResponse>(`/v1/settings/cloud-credentials${q}`);
}

export async function rescanCloudCredentials(): Promise<CloudCredentialsResponse> {
  return cloudApi<CloudCredentialsResponse>('/v1/settings/cloud-credentials/scan', { method: 'POST' });
}

export async function patchCloudCredential(
  provider: string,
  body: Record<string, string>,
): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export async function approveCloudCredential(provider: string): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}/approve`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function rejectCloudCredential(
  provider: string,
  reason = '',
): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export async function revokeCloudCredential(provider: string): Promise<CloudCredentialSource> {
  return cloudApi<CloudCredentialSource>(`/v1/settings/cloud-credentials/${provider}/revoke`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
