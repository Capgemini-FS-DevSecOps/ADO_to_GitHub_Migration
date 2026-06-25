import { ACCEL } from './api';
import type { SSEEvent } from './types/agent';

export const AGENT = process.env.NEXT_PUBLIC_AGENT_URL || 'http://localhost:8090';

const creds: RequestInit = { credentials: 'include' };

const AGENT_REQUEST_TIMEOUT_MS = 15_000;
const AGENT_HEALTH_TIMEOUT_MS = 5_000;

async function agentFetch(url: string, init?: RequestInit, timeoutMs = AGENT_REQUEST_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { cache: 'no-store', ...creds, ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function agentApi<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await agentFetch(`${AGENT}${path}`, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(
        `Agent API at ${AGENT} did not respond in time. The accelerator may be busy or hung — restart both services.`,
      );
    }
    throw new Error(
      `Cannot reach Agent API at ${AGENT}. Ensure the agent container is running on port 8090 (docker compose up agent).`,
    );
  }
  if (!r.ok) {
    const err = await r.text();
    if (r.status === 401) {
      throw new Error('Sign in required — log in at /login, then retry the agent action.');
    }
    try {
      const parsed = JSON.parse(err) as { detail?: string };
      if (parsed.detail) {
        throw new Error(String(parsed.detail));
      }
    } catch (parseErr) {
      if (parseErr instanceof Error && parseErr.message !== err) {
        throw parseErr;
      }
    }
    throw new Error(err || `Agent API ${path} failed (${r.status})`);
  }
  return r.json();
}

export type AgentHealth = {
  status: string;
  accelerator_reachable?: boolean;
  remediation_steps?: string[];
  llm_degraded?: boolean;
  llm_unconfigured?: boolean;
  models_configured?: number;
  selected_model_id?: string | null;
  profile?: string;
};

export type MigrationPlan = {
  phase?: string;
  repo_count?: number;
  repo_order?: string[];
  narrative?: string;
  blocked?: boolean;
  block_reason?: string;
  pipeline_steps?: string[];
  dry_run?: boolean;
  work_items?: Array<{
    id: string;
    label: string;
    category?: string;
    category_label?: string;
    status: string;
    blocker?: string;
    repo?: string;
    scope?: string;
  }>;
  work_summary?: Record<string, number>;
};

export type AgentTask = {
  id: string;
  label: string;
  subagent: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'blocked' | 'skipped';
  detail?: string;
  blocker?: string;
  category?: string;
  category_label?: string;
  count?: number;
  updated_at?: string;
};

export type AgentFormField = {
  name: string;
  label: string;
  type: 'select' | 'checkbox' | 'text' | 'textarea';
  options?: Array<string | { value: string; label: string }>;
  required?: boolean;
};

export type AgentPendingForm = {
  form_id: string;
  title: string;
  description?: string;
  fields: AgentFormField[];
  submit_action?: string;
};

export type AgentMessage = {
  role: string;
  content: string;
  kind?: 'message' | 'thinking' | 'tool_call' | 'tool_result' | 'form' | 'task_update' | 'progress' | 'status' | 'token' | 'heartbeat' | 'form_request' | 'done';
  subagent?: string;
  timestamp?: string;
  meta?: Record<string, unknown>;
};

export type AgentExecutionPolicy = {
  dry_run?: boolean;
  requires_live_approval?: boolean;
  live_approved?: boolean;
  can_execute_live_without_approval?: boolean;
  can_approve_live_execution?: boolean;
  user_role?: string;
  user_display_name?: string;
};

export type AgentSession = {
  session_id: string;
  profile_id?: string;
  status: string;
  subagent?: string;
  dry_run?: boolean;
  selected_model_id?: string | null;
  llm_degraded?: boolean;
  llm_unconfigured?: boolean;
  live_approval_id?: string | null;
  live_approval_status?: string | null;
  user_role?: string;
  user_display_name?: string;
  permissions?: Record<string, boolean>;
  execution_policy?: AgentExecutionPolicy;
  migration_plan?: MigrationPlan | null;
  discovery_snapshot?: Record<string, unknown> | null;
  discovery_fetched_at?: string | null;
  pipeline_run_id?: string | null;
  messages?: AgentMessage[];
  thinking_log?: AgentMessage[];
  tasks?: AgentTask[];
  pending_form?: AgentPendingForm | null;
  run_status?: string;
  steps?: Array<Record<string, unknown>>;
  approval?: { required?: boolean; approved?: boolean; approval_id?: string } | null;
  reply?: string;
};

export async function fetchAgentHealth(): Promise<AgentHealth> {
  let r: Response;
  try {
    r = await agentFetch(`${AGENT}/health`, undefined, AGENT_HEALTH_TIMEOUT_MS);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(
        `Agent API at ${AGENT} did not respond in time. Restart accelerator and agent, then retry.`,
      );
    }
    throw err;
  }
  if (!r.ok) {
    const err = await r.text();
    throw new Error(err || `Agent health check failed (${r.status})`);
  }
  return r.json();
}

export async function createAgentSession(body: {
  profile_id: string;
  prompt: string;
  dry_run?: boolean;
  assignment_id?: string;
  model_id?: string;
}) {
  return agentApi<AgentSession>('/v1/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function getAgentSession(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}`);
}

export type AgentSessionSummary = {
  session_id: string;
  profile_id?: string;
  title: string;
  status?: string;
  updated_at?: string;
  created_at?: string;
  message_count?: number;
};

export async function listAgentSessions(profileId: string) {
  const q = new URLSearchParams({ profile_id: profileId });
  return agentApi<{ sessions: AgentSessionSummary[] }>(`/v1/sessions?${q}`);
}

export async function deleteAgentSession(sessionId: string) {
  return agentApi<{ deleted: string }>(`/v1/sessions/${sessionId}`, { method: 'DELETE' });
}

export async function postAgentMessage(sessionId: string, message: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
}

/** T067: Stream event type aligned with SSEEvent from types/agent.ts */
export type StreamEvent = SSEEvent & {
  __done__?: boolean;
  reply?: string;
  pending_form?: Record<string, unknown>;
};

export async function streamAgentMessage(
  sessionId: string,
  message: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<AgentSession> {
  const r = await fetch(`${AGENT}/v1/sessions/${sessionId}/message-stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ message }),
    signal,
  });

  if (!r.ok) {
    const err = await r.text();
    try {
      const parsed = JSON.parse(err) as { detail?: string };
      throw new Error(parsed.detail || err);
    } catch {
      throw new Error(err || `Agent API stream failed (${r.status})`);
    }
  }

  const reader = r.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const evt = JSON.parse(line.slice(6)) as StreamEvent;
          // T067: Skip heartbeat events — they're just keepalive
          if (evt.kind === 'heartbeat') continue;
          onEvent(evt);
        } catch {
          // skip malformed
        }
      }
    }
  }

  const final = await fetch(`${AGENT}/v1/sessions/${sessionId}`, {
    credentials: 'include',
    cache: 'no-store',
  });
  if (final.ok) {
    return final.json() as Promise<AgentSession>;
  }
  throw new Error('Failed to fetch final session state');
}

export async function patchAgentExecutionMode(sessionId: string, dry_run: boolean) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/execution-mode`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run }),
  });
}

export async function requestAgentLive(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/request-live`, { method: 'POST' });
}

export async function approveAgentSession(sessionId: string, approved: boolean, reason = '') {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved, reason }),
  });
}

export async function submitAgentForm(sessionId: string, values: Record<string, unknown>) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/form-submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
}

export async function streamAgentFormSubmit(
  sessionId: string,
  values: Record<string, unknown>,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<AgentSession> {
  const r = await fetch(`${AGENT}/v1/sessions/${sessionId}/form-submit-stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ values }),
    signal,
  });

  if (!r.ok) {
    const err = await r.text();
    try {
      const parsed = JSON.parse(err) as { detail?: string };
      throw new Error(parsed.detail || err);
    } catch {
      throw new Error(err || `Agent API stream failed (${r.status})`);
    }
  }

  const reader = r.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const evt = JSON.parse(line.slice(6)) as StreamEvent;
          if (evt.kind === 'heartbeat') continue;
          onEvent(evt);
        } catch {
          // skip malformed
        }
      }
    }
  }

  const final = await fetch(`${AGENT}/v1/sessions/${sessionId}`, {
    credentials: 'include',
    cache: 'no-store',
  });
  if (final.ok) {
    return final.json() as Promise<AgentSession>;
  }
  throw new Error('Failed to fetch final session state');
}

export async function cancelAgentForm(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/form-cancel`, { method: 'POST' });
}

/** T072: Cancel session with optional rollback (FR-082, FR-083) */
export async function cancelAgentSession(sessionId: string, action: 'stop' | 'rollback' = 'stop') {
  return agentApi<{ status: string; rollback: string; message: string; session_id: string }>(
    `/v1/sessions/${sessionId}/cancel`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    },
  );
}

/** T072: Get plan summary for user confirmation (FR-014) */
export async function getPlanSummary(sessionId: string) {
  return agentApi<{
    session_id: string;
    dry_run: boolean;
    repos: string[];
    work_items: Array<{
      repo: string;
      scopes: string[];
      status: string;
      blocked_reasons: string[];
    }>;
    assumptions: string[];
    revision: number;
    requires_confirmation: boolean;
  }>(`/v1/sessions/${sessionId}/plan-summary`);
}

/** T072: Confirm live execution (CA-001) */
export async function confirmLiveExecution(sessionId: string) {
  return agentApi<{ status: string; dry_run: boolean; session_id: string }>(
    `/v1/sessions/${sessionId}/confirm-live`,
    { method: 'POST' },
  );
}

/** T072: Fetch Prometheus metrics (FR-069) */
export async function fetchAgentMetrics(): Promise<string> {
  const r = await fetch(`${AGENT}/metrics`, { cache: 'no-store', ...creds });
  if (!r.ok) throw new Error('Failed to fetch metrics');
  return r.text();
}

export async function fetchLlmModels() {
  let r: Response;
  try {
    r = await fetch(`${AGENT}/v1/agent/models`, { cache: 'no-store', ...creds });
  } catch {
    throw new Error(`Cannot reach Agent API at ${AGENT} to load LLM models.`);
  }
  if (!r.ok) {
    if (r.status === 401) {
      throw new Error('Sign in required — log in at /login to load LLM models on the agent page.');
    }
    throw new Error('Failed to load LLM models');
  }
  return r.json() as Promise<{ models: AgentModel[]; default_model_id?: string | null }>;
}

export type AgentModel = {
  id: string;
  provider: string;
  provider_label: string;
  model_id: string;
  credential_mode?: string;
  cloud_provider?: string | null;
  platform_supplied?: boolean;
  source_approved?: boolean | null;
  enabled?: boolean;
  validation_status?: string;
};

export function formatAgentModelLabel(model: AgentModel): string {
  const platform = model.provider_label || model.provider;
  const modelName = model.model_id || model.id;
  return `${platform} : ${modelName}`;
}
