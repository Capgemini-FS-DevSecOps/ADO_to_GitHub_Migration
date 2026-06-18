import { ACCEL } from './api';

export type CatalogEntry = {
  id: string;
  display_name: string;
  description?: string;
  provider: string;
  source: 'preset' | 'live' | 'override';
};

export type CatalogResponse = {
  entries: CatalogEntry[];
  source: string;
  stale: boolean;
  discovery_error?: string;
  resolved_base_url?: string;
};

export type ConnectivityProfile = {
  proxy_enabled: boolean;
  proxy_host: string;
  proxy_port: number;
  proxy_username: string;
  proxy_password: string;
  custom_ca_configured: boolean;
  allow_custom_model_id: boolean;
  updated_at: string;
  updated_by: string;
};

export type ValidationResult = {
  status: 'passed' | 'failed';
  category: string | null;
  message: string;
  validated_at: string;
};

export type LlmModelRecord = {
  id: string;
  display_name: string;
  provider: string;
  model_id: string;
  api_key: string;
  enabled: boolean;
  default_for_agent: boolean;
  validation_status: 'never_validated' | 'passed' | 'failed';
  validation_at: string | null;
  catalog_source: string | null;
  catalog_label: string | null;
  base_url: string | null;
};

async function llmFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${ACCEL}${path}`, { credentials: 'include', cache: 'no-store', ...init });
  } catch {
    throw new Error(
      `Cannot reach Accelerator API at ${ACCEL}. If using Docker, ensure the accelerator container is running on port 8080.`,
    );
  }
  if (!response.ok) {
    const text = await response.text();
    if (response.status === 401) {
      throw new Error('Sign in required — log in as an admin at /login, then retry.');
    }
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) {
        throw new Error(parsed.detail);
      }
    } catch (parseErr) {
      if (parseErr instanceof Error && parseErr.message !== text) {
        throw parseErr;
      }
    }
    throw new Error(text || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function fetchCatalog(params: {
  provider: string;
  apiKey?: string;
  baseUrl?: string;
}): Promise<CatalogResponse> {
  const query = new URLSearchParams({ provider: params.provider });
  if (params.apiKey) query.set('api_key', params.apiKey);
  if (params.baseUrl) query.set('base_url', params.baseUrl);
  return llmFetch<CatalogResponse>(`/v1/settings/llm-models/catalog?${query.toString()}`);
}

export async function fetchConnectivity(): Promise<ConnectivityProfile> {
  return llmFetch<ConnectivityProfile>('/v1/settings/connectivity');
}

export async function updateConnectivity(body: Record<string, unknown>): Promise<ConnectivityProfile> {
  return llmFetch<ConnectivityProfile>('/v1/settings/connectivity', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function testConnectivity(): Promise<ValidationResult> {
  return llmFetch<ValidationResult>('/v1/settings/connectivity/test', { method: 'POST' });
}

export async function validateModel(body: Record<string, unknown>): Promise<ValidationResult> {
  return llmFetch<ValidationResult>('/v1/settings/llm-models/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function validateSavedModel(modelId: string): Promise<ValidationResult> {
  return llmFetch<ValidationResult>(`/v1/settings/llm-models/${modelId}/validate`, {
    method: 'POST',
  });
}

export async function saveModel(body: Record<string, unknown>): Promise<LlmModelRecord> {
  return llmFetch<LlmModelRecord>('/v1/settings/llm-models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function deleteModel(modelId: string): Promise<{ deleted: string }> {
  return llmFetch<{ deleted: string }>(`/v1/settings/llm-models/${modelId}`, {
    method: 'DELETE',
  });
}

export function canEnableModel(validationStatus: string | undefined): boolean {
  return validationStatus === 'passed';
}

export function validationBadgeLabel(status: string | undefined): string {
  if (status === 'passed') return 'Passed';
  if (status === 'failed') return 'Failed';
  return 'Not validated';
}
