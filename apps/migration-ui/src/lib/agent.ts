import { ACCEL } from './api';

export const AGENT = process.env.NEXT_PUBLIC_AGENT_URL || 'http://localhost:8090';

const creds: RequestInit = { credentials: 'include' };

async function agentApi<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${AGENT}${path}`, { cache: 'no-store', ...creds, ...init });
  } catch {
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
  options?: string[];
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
  kind?: 'message' | 'thinking' | 'tool_call' | 'tool_result' | 'form' | 'task_update' | 'progress';
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
  pipeline_run_id?: string | null;
  messages?: AgentMessage[];
  tasks?: AgentTask[];
  pending_form?: AgentPendingForm | null;
  run_status?: string;
  steps?: Array<Record<string, unknown>>;
  approval?: { required?: boolean; approved?: boolean; approval_id?: string } | null;
  reply?: string;
};

export async function fetchAgentHealth(): Promise<AgentHealth> {
  return agentApi<AgentHealth>('/health');
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

export async function cancelAgentForm(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/form-cancel`, { method: 'POST' });
}

export async function fetchLlmModels() {
  let r: Response;
  try {
    r = await fetch(`${ACCEL}/v1/settings/llm-models`, { cache: 'no-store', ...creds });
  } catch {
    throw new Error(`Cannot reach Accelerator API at ${ACCEL} to load LLM models.`);
  }
  if (!r.ok) {
    if (r.status === 401) {
      throw new Error('Sign in required — log in at /login to load LLM models on the agent page.');
    }
    throw new Error('Failed to load LLM models');
  }
  return r.json() as Promise<{ models: Array<Record<string, unknown>> }>;
}
