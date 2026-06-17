import { ACCEL } from './api';

export const AGENT = process.env.NEXT_PUBLIC_AGENT_URL || 'http://localhost:8090';

const creds: RequestInit = { credentials: 'include' };

async function agentApi<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${AGENT}${path}`, { cache: 'no-store', ...creds, ...init });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(err || `Agent API ${path} failed (${r.status})`);
  }
  return r.json();
}

export type AgentHealth = {
  status: string;
  accelerator_reachable?: boolean;
  remediation_steps?: string[];
  llm_provider?: string;
  profile?: string;
};

export type AgentSession = {
  session_id: string;
  status: string;
  subagent?: string;
  dry_run?: boolean;
  selected_model_id?: string | null;
  llm_degraded?: boolean;
  live_approval_id?: string | null;
  live_approval_status?: string | null;
  messages?: Array<{ role: string; content: string }>;
  run_status?: string;
  steps?: Array<Record<string, unknown>>;
  approval?: { required?: boolean; approved?: boolean; approval_id?: string } | null;
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

export async function postAgentMessage(sessionId: string, message: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
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

export async function fetchLlmModels() {
  const r = await fetch(`${ACCEL}/v1/settings/llm-models`, { cache: 'no-store', ...creds });
  if (!r.ok) throw new Error('Failed to load LLM models');
  return r.json() as Promise<{ models: Array<Record<string, unknown>> }>;
}
