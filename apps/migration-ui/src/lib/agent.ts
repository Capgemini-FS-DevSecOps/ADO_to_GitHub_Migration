import { ACCEL } from './api';
import type { SSEEvent } from './types/agent';

/** Base URL of the `services/agent` plan-execute-validate loop (PEV) API, from NEXT_PUBLIC_AGENT_URL or localhost:8090. */
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

export type AgentFormFieldOption = {
  value: string;
  label: string;
  description?: string;
  recommended?: boolean;
};

export type AgentFormField = {
  name: string;
  label: string;
  type: 'select' | 'checkbox' | 'text' | 'textarea';
  options?: Array<string | AgentFormFieldOption>;
  required?: boolean;
  description?: string;
  placeholder?: string;
  /**
   * Boolean fields carry a real JSON boolean (GAP-024); everything else is a string.
   * Older payloads stringified booleans, so `"False"` can still arrive and is parsed
   * by `parseBooleanValue`.
   */
  recommended_value?: string | boolean;
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

/**
 * Check agent liveness (GET /health) on a short timeout and return the health record,
 * including accelerator reachability and LLM configuration state.
 */
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

/** Create an agent session (POST /v1/sessions) and return the new session record. */
export async function createAgentSession(body: {
  profile_id: string;
  prompt: string;
  dry_run?: boolean;
  model_id?: string;
}) {
  return agentApi<AgentSession>('/v1/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Fetch one session's current state (GET /v1/sessions/{id}), messages and tasks included. */
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

/** List a profile's sessions (GET /v1/sessions) and return their summaries. */
export async function listAgentSessions(profileId: string) {
  const q = new URLSearchParams({ profile_id: profileId });
  return agentApi<{ sessions: AgentSessionSummary[] }>(`/v1/sessions?${q}`);
}

/** Delete a session (DELETE /v1/sessions/{id}) and return the deleted session id. */
export async function deleteAgentSession(sessionId: string) {
  return agentApi<{ deleted: string }>(`/v1/sessions/${sessionId}`, { method: 'DELETE' });
}

/** Send a chat message (POST /v1/sessions/{id}/message) and return the updated session. */
export async function postAgentMessage(sessionId: string, message: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
}

/** Stream event type aligned with SSEEvent from types/agent.ts (task id T067) */
export type StreamEvent = SSEEvent & {
  __done__?: boolean;
  reply?: string;
  pending_form?: Record<string, unknown>;
};

/**
 * Send a chat message over a server-sent event stream (SSE) (POST /v1/sessions/{id}/message-stream), invoking the
 * callback for each event except heartbeats, then return the final session state.
 */
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
          // Skip heartbeat events — they're just keepalive (task id T067)
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

/** Ask for live (non dry-run) execution (POST /v1/sessions/{id}/request-live). */
export async function requestAgentLive(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/request-live`, { method: 'POST' });
}

/** Approve or deny a session's live run (POST /v1/sessions/{id}/approve) with an optional reason. */
export async function approveAgentSession(
  sessionId: string,
  approved: boolean,
  reason: string = '',
) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved, reason }),
  });
}

/** Submit a pending human-in-the-loop operator prompt form (POST /v1/sessions/{id}/form-submit) and return the session. */
export async function submitAgentForm(sessionId: string, values: Record<string, unknown>) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/form-submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
}

/**
 * Submit a pending human-in-the-loop operator prompt form over a server-sent event stream (SSE) (POST /v1/sessions/{id}/form-submit-stream),
 * invoking the callback for each event except heartbeats, then return the final session.
 */
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

/** Dismiss the pending human-in-the-loop operator prompt form (POST /v1/sessions/{id}/form-cancel) without answering it. */
export async function cancelAgentForm(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/form-cancel`, { method: 'POST' });
}

/** Cancel session with optional rollback (task id T072; FR-082, FR-083) */
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

/**
 * Load the configured LLM models (GET /v1/agent/models) and the default model id for the
 * agent model picker.
 */
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

/** Build the picker label for a model as "provider : model", falling back to its ids. */
export function formatAgentModelLabel(model: AgentModel): string {
  const platform = model.provider_label || model.provider;
  const modelName = model.model_id || model.id;
  return `${platform} : ${modelName}`;
}
