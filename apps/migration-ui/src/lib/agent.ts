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
  models_configured?: number;
  selected_model_id?: string | null;
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

export async function runAgentPev(sessionId: string) {
  return agentApi<AgentSession>(`/v1/sessions/${sessionId}/run-pev`, { method: 'POST' });
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
