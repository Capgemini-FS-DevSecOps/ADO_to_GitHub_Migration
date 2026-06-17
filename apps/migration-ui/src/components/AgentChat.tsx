'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { fetchSettings } from '@/lib/api';
import { fetchSession } from '@/lib/auth';
import { canApproveLiveExecution, canOperate } from '@/lib/permissions';
import {
  approveAgentSession,
  AGENT,
  createAgentSession,
  fetchAgentHealth,
  fetchLlmModels,
  getAgentSession,
  postAgentMessage,
  requestAgentLive,
  runAgentPev,
  type AgentSession,
} from '@/lib/agent';

type ChatMessage = {
  id: string;
  role: string;
  content: string;
};

const STUB_MODEL = { id: 'stub', display_name: 'Stub (offline)', provider: 'stub' };

export function AgentChat() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const { data: health, isError: agentHealthError, error: agentHealthErrorObj } = useQuery({
    queryKey: ['agent-health'],
    queryFn: fetchAgentHealth,
    retry: 1,
  });
  const { data: llmData, isError: modelsError, error: modelsErrorObj } = useQuery({
    queryKey: ['llm-models'],
    queryFn: fetchLlmModels,
  });

  const activeProfiles = (settings?.migration_profiles ?? []).filter(
    (p) => p.status === 'active' || !p.status,
  );
  const enabledModels = (llmData?.models ?? []).filter(
    (m) => m.enabled !== false && String(m.provider) !== 'stub',
  );
  const models = enabledModels.length ? enabledModels : llmData?.models?.length ? llmData.models : [STUB_MODEL];
  const canApproveLive = canApproveLiveExecution(session?.permissions);
  const canOperateAgent = canOperate(session?.permissions) !== false;

  const [profileId, setProfileId] = useState('');
  const [modelId, setModelId] = useState('stub');
  const [agentSession, setAgentSession] = useState<AgentSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!profileId && activeProfiles[0]?.id) {
      setProfileId(activeProfiles[0].id);
    }
  }, [activeProfiles, profileId]);

  useEffect(() => {
    const defaultModel = models.find((m) => (m as { default_for_agent?: boolean }).default_for_agent);
    const firstEnabled = models.find((m) => String(m.id) !== 'stub');
    const pick = defaultModel ?? firstEnabled;
    if (pick && modelId === 'stub') {
      setModelId(String(pick.id));
    }
  }, [models, modelId]);

  useEffect(() => {
    if (!agentSession || agentSession.status !== 'awaiting_approval') return;
    const id = setInterval(async () => {
      try {
        const s = await getAgentSession(agentSession.session_id);
        setAgentSession(s);
        syncMessages(s);
        if (s.status !== 'awaiting_approval') clearInterval(id);
      } catch {
        /* ignore poll errors */
      }
    }, 10_000);
    return () => clearInterval(id);
  }, [agentSession?.session_id, agentSession?.status]);

  const syncMessages = (s: AgentSession) => {
    const msgs = (s.messages ?? []).map((m, i) => ({
      id: `${m.role}-${i}`,
      role: m.role,
      content: m.content,
    }));
    if (msgs.length) setMessages(msgs);
  };

  const pollSession = async (sessionId: string) => {
    setPolling(true);
    try {
      for (let i = 0; i < 30; i++) {
        const s = await getAgentSession(sessionId);
        setAgentSession(s);
        syncMessages(s);
        if (['completed', 'failed', 'awaiting_approval', 'idle'].includes(s.status)) break;
        await new Promise((r) => setTimeout(r, 800));
      }
    } finally {
      setPolling(false);
    }
  };

  const startSession = useMutation({
    mutationFn: async (prompt: string) => {
      const pid = profileId || activeProfiles[0]?.id || 'lightweight';
      return createAgentSession({
        profile_id: pid,
        prompt,
        dry_run: true,
        model_id: modelId !== 'stub' ? modelId : undefined,
      });
    },
    onSuccess: async (s) => {
      setAgentSession(s);
      setError(null);
      const full = await getAgentSession(s.session_id);
      setAgentSession(full);
      syncMessages(full);
      if (full.status !== 'idle') {
        await pollSession(s.session_id);
      }
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Session failed'),
  });

  const [pevRunning, setPevRunning] = useState(false);

  const runPev = async () => {
    if (!agentSession || pevRunning) return;
    setPevRunning(true);
    setError(null);
    try {
      await runAgentPev(agentSession.session_id);
      await pollSession(agentSession.session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'PEV run failed');
    } finally {
      setPevRunning(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: 'user', content: text }]);
    if (!agentSession) {
      startSession.mutate(text);
      return;
    }
    try {
      const s = await postAgentMessage(agentSession.session_id, text);
      setAgentSession(s);
      syncMessages(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Message failed');
    }
  };

  const phaseLabel = agentSession?.subagent || agentSession?.status || 'idle';
  const agentUnreachable = agentHealthError;
  const llmDegraded =
    !agentUnreachable &&
    (Boolean(health?.llm_degraded) ||
      Boolean(agentSession?.llm_degraded) ||
      (enabledModels.length === 0 && !modelsError));

  return (
    <div className="agent-chat-shell">
      {agentUnreachable && (
        <div className="oai-card" style={{ marginBottom: 12, borderColor: 'var(--warning)' }}>
          <p>
            {agentHealthErrorObj instanceof Error
              ? agentHealthErrorObj.message
              : `Cannot reach Agent API at ${AGENT}.`}
          </p>
        </div>
      )}

      {!agentUnreachable && health && !health.accelerator_reachable && (
        <div className="oai-card" style={{ marginBottom: 12, borderColor: 'var(--warning)' }}>
          <p>Agent cannot reach the accelerator API.</p>
          {health.remediation_steps?.map((step) => (
            <p key={step} className="form-hint">{step}</p>
          ))}
        </div>
      )}

      {modelsError && (
        <div className="oai-card" style={{ marginBottom: 12, borderColor: 'var(--warning)' }}>
          <p>{modelsErrorObj instanceof Error ? modelsErrorObj.message : 'Failed to load LLM models.'}</p>
        </div>
      )}

      {!agentUnreachable && llmDegraded && health?.remediation_steps?.length ? (
        <div className="oai-card" style={{ marginBottom: 12, borderColor: 'var(--warning)' }}>
          {health.remediation_steps.map((step) => (
            <p key={step} className="form-hint">{step}</p>
          ))}
        </div>
      ) : null}

      <div className="agent-model-bar">
        <label htmlFor="agent-profile-select" className="agent-model-label">Deployment profile</label>
        <select
          id="agent-profile-select"
          className="oai-input agent-model-select"
          value={profileId || activeProfiles[0]?.id || ''}
          onChange={(e) => setProfileId(e.target.value)}
        >
          {activeProfiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}{p.is_default ? ' (default)' : ''}
            </option>
          ))}
        </select>
        <label htmlFor="agent-model-select" className="agent-model-label">Model</label>
        <select
          id="agent-model-select"
          className="oai-input agent-model-select"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
        >
          {models.map((m) => (
            <option key={String(m.id)} value={String(m.id)}>
              {String(m.display_name)} · {String(m.provider)}
            </option>
          ))}
        </select>
        <span className="agent-model-hint">
          PEV: <strong>{phaseLabel}</strong>
          {agentSession?.dry_run ? ' · dry-run' : ''}
          {llmDegraded ? ' · stub/degraded LLM' : ''}
        </span>
      </div>

      {llmDegraded && !agentSession && !agentUnreachable && (
        <p className="form-hint" style={{ marginBottom: 8 }}>
          No live model configured — add, validate, and enable a model under Settings → LLM models.
        </p>
      )}

      {agentSession?.status === 'awaiting_approval' && canApproveLive && (
        <div style={{ marginBottom: 12, display: 'flex', gap: 8 }}>
          <button
            type="button"
            className="oai-button oai-button-primary"
            onClick={() =>
              approveAgentSession(agentSession.session_id, true).then((s) => {
                setAgentSession(s);
                pollSession(s.session_id);
              })
            }
          >
            Approve live run
          </button>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            onClick={() =>
              approveAgentSession(agentSession.session_id, false, 'Denied').then((s) => {
                setAgentSession(s);
              })
            }
          >
            Deny
          </button>
        </div>
      )}

      {agentSession?.status === 'awaiting_approval' && !canApproveLive && (
        <p className="form-hint">
          Live execution pending platform approval
          {agentSession.live_approval_id ? ` (${agentSession.live_approval_id})` : ''}.
        </p>
      )}

      {canOperateAgent && agentSession?.status === 'idle' && (
        <div style={{ marginBottom: 12, display: 'flex', gap: 8 }}>
          <button
            type="button"
            className="oai-button oai-button-primary"
            disabled={pevRunning || polling}
            onClick={runPev}
          >
            {pevRunning || polling ? 'Running PEV…' : 'Run migration plan (PEV dry-run)'}
          </button>
        </div>
      )}

      {canOperateAgent && agentSession && agentSession.status !== 'awaiting_approval' && agentSession.status !== 'idle' && !canApproveLive && (
        <button
          type="button"
          className="oai-button oai-button-secondary"
          style={{ marginBottom: 8 }}
          onClick={() => requestAgentLive(agentSession.session_id).then(setAgentSession)}
        >
          Request live execution
        </button>
      )}

      <div className="agent-chat-messages" ref={listRef}>
        {messages.map((msg) => (
          <div key={msg.id} className={`agent-chat-bubble agent-chat-${msg.role === 'user' ? 'user' : 'assistant'}`}>
            <div className="agent-chat-meta"><span>{msg.role}</span></div>
            <p>{msg.content}</p>
          </div>
        ))}
        {(startSession.isPending || pevRunning || polling) && (
          <div className="agent-chat-bubble agent-chat-assistant agent-chat-typing">
            <span className="agent-typing-dots">
              {startSession.isPending ? 'Thinking…' : 'PEV running…'}
            </span>
          </div>
        )}
      </div>

      {error && <p className="oai-error">{error}</p>}

      <div className="agent-chat-composer">
        <textarea
          className="oai-input agent-chat-input"
          rows={2}
          placeholder="Describe migration goal or ask a question…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="button"
          className="oai-button oai-button-primary agent-chat-send"
          disabled={!input.trim() || startSession.isPending || pevRunning || polling}
          onClick={send}
        >
          Send
        </button>
      </div>
    </div>
  );
}
