'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { fetchSettings } from '@/lib/api';
import { fetchSession } from '@/lib/auth';
import { canApproveLiveExecution, canOperate } from '@/lib/permissions';
import {
  approveAgentSession,
  AGENT,
  cancelAgentForm,
  createAgentSession,
  deleteAgentSession,
  fetchAgentHealth,
  fetchLlmModels,
  getAgentSession,
  listAgentSessions,
  postAgentMessage,
  requestAgentLive,
  patchAgentExecutionMode,
  submitAgentForm,
  type AgentMessage,
  type AgentPendingForm,
  type AgentSession,
  type AgentSessionSummary,
  type AgentTask,
} from '@/lib/agent';
import {
  chatCacheKey,
  loadActiveSessionId,
  loadSessionIndex,
  mergeSessionLists,
  removeSessionIndex,
  saveActiveSessionId,
  truncateTitle,
  upsertSessionIndex,
  type SessionIndexEntry,
} from '@/lib/agentSessions';
import { MarkdownMessage } from '@/components/MarkdownMessage';

type ChatMessage = AgentMessage & { id: string };

const NO_MODELS_HINT =
  'No LLM models are configured. Add, validate, and enable a model under Settings → LLM models.';

const TASK_STATUS_LABEL: Record<string, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Done',
  failed: 'Failed',
  blocked: 'Blocked',
  skipped: 'Skipped',
  ready: 'Ready',
};

const CATEGORY_BADGE: Record<string, string> = {
  migrate_repo: 'Repo',
  convert_metadata: 'Convert',
  manual_setup: 'Manual',
};

function AgentTaskTimeline({ tasks }: { tasks: AgentTask[] }) {
  if (!tasks.length) return null;
  return (
    <div className="agent-task-list" aria-label="Migration tasks">
      {tasks.map((task) => (
        <div key={task.id} className={`agent-task-item agent-task-${task.status}`}>
          <span className="agent-task-status">{TASK_STATUS_LABEL[task.status] ?? task.status}</span>
          {task.category ? (
            <span className="agent-task-category" title={task.category_label}>
              {CATEGORY_BADGE[task.category] ?? task.category_label ?? task.category}
            </span>
          ) : null}
          <span className="agent-task-label">{task.label}</span>
          <span className="agent-task-subagent">{task.subagent}</span>
          {(task.blocker || task.detail) ? (
            <span className="agent-task-detail">{task.blocker || task.detail}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function isInternalMessage(msg: ChatMessage): boolean {
  if (msg.role === 'user') return false;
  if (msg.kind === 'message' || msg.kind === 'form') {
    return msg.role === 'planner' || msg.role === 'executor';
  }
  return (
    msg.kind === 'thinking' ||
    msg.kind === 'tool_call' ||
    msg.kind === 'tool_result' ||
    msg.kind === 'task_update' ||
    msg.kind === 'progress' ||
    msg.role === 'tool' ||
    msg.role === 'planner' ||
    msg.role === 'executor'
  );
}

function internalStepLabel(msg: ChatMessage): string {
  if (msg.kind === 'tool_call') return msg.content;
  if (msg.kind === 'tool_result') return String(msg.meta?.tool ?? 'result');
  if (msg.kind === 'thinking') return 'thinking';
  if (msg.kind === 'progress') return msg.subagent ?? msg.role;
  return msg.kind ?? msg.role;
}

function renderInternalMessage(msg: ChatMessage) {
  if (msg.kind === 'thinking') {
    return (
      <div key={msg.id} className="agent-work-step">
        <span className="agent-work-step-label">Thinking</span>
        <p>{msg.content}</p>
      </div>
    );
  }
  if (msg.kind === 'tool_call') {
    const args = msg.meta?.arguments as Record<string, unknown> | undefined;
    return (
      <div key={msg.id} className="agent-work-step">
        <span className="agent-work-step-label">{msg.content}</span>
        {args && Object.keys(args).length > 0 ? (
          <code className="agent-tool-args">{JSON.stringify(args)}</code>
        ) : null}
      </div>
    );
  }
  if (msg.kind === 'tool_result') {
    return (
      <div key={msg.id} className="agent-work-step">
        <span className="agent-work-step-label">{String(msg.meta?.tool ?? 'tool')}</span>
        <p>{msg.content}</p>
      </div>
    );
  }
  return (
    <div key={msg.id} className="agent-work-step">
      <span className="agent-work-step-label">{internalStepLabel(msg)}</span>
      <p>{msg.content}</p>
    </div>
  );
}

function AgentWorkBlock({ messages }: { messages: ChatMessage[] }) {
  const labels = messages.map(internalStepLabel).filter(Boolean);
  const summary =
    labels.length <= 2
      ? labels.join(' · ')
      : `${labels.slice(0, 2).join(' · ')} · +${labels.length - 2} more`;

  return (
    <details className="agent-work-block">
      <summary>
        <span className="agent-work-summary">Worked</span>
        <span className="agent-work-meta">
          {messages.length} step{messages.length === 1 ? '' : 's'}
          {summary ? ` · ${summary}` : ''}
        </span>
      </summary>
      <div className="agent-work-inner">{messages.map(renderInternalMessage)}</div>
    </details>
  );
}

function AgentFormPanel({
  form,
  busy,
  onSubmit,
  onCancel,
  executionPolicy,
}: {
  form: AgentPendingForm;
  busy: boolean;
  onSubmit: (values: Record<string, unknown>) => void;
  onCancel: () => void;
  executionPolicy?: AgentSession['execution_policy'];
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    const initial: Record<string, unknown> = {};
    for (const field of form.fields) {
      if (field.type === 'select' && field.options?.length) {
        initial[field.name] = field.options[0];
      } else if (field.type === 'checkbox') {
        initial[field.name] = field.name === 'confirm_execute';
      }
    }
    setValues(initial);
    setLocalError(null);
  }, [form.form_id, form.fields]);

  const handleSubmit = () => {
    const phaseField = form.fields.find((f) => f.name === 'phase');
    if (phaseField?.required && !values.phase) {
      setLocalError('Select a migration phase.');
      return;
    }
    if (form.form_id === 'plan_confirmation') {
      const notes = String(values.plan_notes ?? '').trim();
      const confirmed = Boolean(values.plan_confirmed);
      if (!confirmed && !notes) {
        setLocalError('Confirm the plan is correct, or describe changes in Notes.');
        return;
      }
    }
    setLocalError(null);
    onSubmit(values);
  };

  return (
    <div
      className={
        form.form_id === 'plan_confirmation'
          ? 'agent-form-panel agent-form-panel--plan'
          : 'agent-form-panel'
      }
    >
      <h4>{form.title}</h4>
      {form.description ? (
        <MarkdownMessage content={form.description} className="agent-form-description" />
      ) : null}
      {localError ? <p className="oai-error">{localError}</p> : null}
      {executionPolicy?.requires_live_approval ? (
        <p className="form-hint agent-approval-hint">
          Signed in as <strong>{executionPolicy.user_display_name ?? executionPolicy.user_role}</strong>
          {' '}({executionPolicy.user_role}). Live execution requires platform approval — only dry-run
          can start from this form.
        </p>
      ) : executionPolicy?.user_role ? (
        <p className="form-hint">
          Signed in as <strong>{executionPolicy.user_display_name ?? executionPolicy.user_role}</strong>
          {' '}({executionPolicy.user_role})
        </p>
      ) : null}
      {form.fields.map((field) => (
        <label key={field.name} className="agent-form-field">
          <span>{field.label}</span>
          {field.type === 'select' ? (
            <select
              className="oai-input"
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
            >
              <option value="">Select…</option>
              {(field.options ?? []).map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          ) : field.type === 'checkbox' ? (
            <input
              type="checkbox"
              disabled={executionPolicy?.requires_live_approval && field.name === 'confirm_execute'}
              checked={Boolean(values[field.name])}
              onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.checked }))}
            />
          ) : field.type === 'textarea' ? (
            <textarea
              className="oai-input agent-form-textarea"
              rows={4}
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
            />
          ) : (
            <input
              className="oai-input"
              type="text"
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
            />
          )}
        </label>
      ))}
      <div className="agent-form-actions">
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={busy}
          onClick={handleSubmit}
        >
          Continue
        </button>
        <button type="button" className="oai-button oai-button-secondary" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

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
  const models = enabledModels;
  const noModelsConfigured = !modelsError && enabledModels.length === 0;
  const canApproveLive = canApproveLiveExecution(session?.permissions);
  const canOperateAgent = canOperate(session?.permissions) !== false;
  const accountKey = session?.user?.username ?? 'anonymous';

  const [profileId, setProfileId] = useState('');
  const [modelId, setModelId] = useState('');
  const [dryRunMode, setDryRunMode] = useState(true);
  const [agentSession, setAgentSession] = useState<AgentSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [messagePending, setMessagePending] = useState(false);
  const [formPending, setFormPending] = useState(false);
  const [sessionList, setSessionList] = useState<SessionIndexEntry[]>([]);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (agentSession?.dry_run !== undefined) {
      setDryRunMode(agentSession.dry_run);
    }
  }, [agentSession?.session_id, agentSession?.dry_run]);

  const handleDryRunModeChange = async (nextDryRun: boolean) => {
    setDryRunMode(nextDryRun);
    if (!agentSession) return;
    if (['planning', 'executing', 'validating', 'running'].includes(agentSession.status ?? '')) {
      return;
    }
    try {
      const s = await patchAgentExecutionMode(agentSession.session_id, nextDryRun);
      setAgentSession(s);
      syncMessages(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not update execution mode');
      setDryRunMode(agentSession.dry_run ?? true);
    }
  };

  const syncSessionIndex = (sessionId: string, title: string, status?: string) => {
    if (!profileId) return;
    upsertSessionIndex(profileId, accountKey, {
      sessionId,
      title: truncateTitle(title),
      updatedAt: new Date().toISOString(),
      status,
    });
    setSessionList(loadSessionIndex(profileId, accountKey));
  };

  const cacheMessages = (sessionId: string, msgs: ChatMessage[]) => {
    if (!profileId || !sessionId) return;
    localStorage.setItem(chatCacheKey(profileId, sessionId, accountKey), JSON.stringify(msgs));
  };

  const loadCachedMessages = (sessionId: string): ChatMessage[] | null => {
    if (!profileId) return null;
    const raw = localStorage.getItem(chatCacheKey(profileId, sessionId, accountKey));
    if (!raw) return null;
    try {
      return JSON.parse(raw) as ChatMessage[];
    } catch {
      return null;
    }
  };

  const refreshSessionList = async (pid: string, userKey: string) => {
    const local = loadSessionIndex(pid, userKey);
    try {
      const remote = await listAgentSessions(pid);
      const merged = mergeSessionLists(
        local,
        (remote.sessions ?? []).map((s: AgentSessionSummary) => ({
          sessionId: s.session_id,
          title: truncateTitle(s.title || 'New chat'),
          updatedAt: s.updated_at || s.created_at || new Date().toISOString(),
          status: s.status,
        })),
      );
      setSessionList(merged);
    } catch {
      setSessionList(local);
    }
  };

  const loadSessionById = async (sessionId: string, expectedProfileId: string) => {
    const known = loadSessionIndex(expectedProfileId, accountKey).some(
      (entry) => entry.sessionId === sessionId,
    );
    try {
      const s = await getAgentSession(sessionId);
      if (s.profile_id && s.profile_id !== expectedProfileId) {
        removeSessionIndex(expectedProfileId, accountKey, sessionId);
        return false;
      }
      setAgentSession(s);
      syncMessages(s);
      saveActiveSessionId(expectedProfileId, accountKey, sessionId);
      const firstUser = (s.messages ?? []).find((m) => m.role === 'user');
      syncSessionIndex(sessionId, firstUser?.content || 'Chat', s.status);
      return true;
    } catch {
      if (!known) return false;
      const cached = loadCachedMessages(sessionId);
      if (cached?.length) {
        setAgentSession({ session_id: sessionId, status: 'idle', messages: cached, profile_id: expectedProfileId });
        setMessages(cached);
        saveActiveSessionId(expectedProfileId, accountKey, sessionId);
        return true;
      }
      return false;
    }
  };

  const startNewChat = () => {
    setAgentSession(null);
    setMessages([]);
    setError(null);
    if (profileId) saveActiveSessionId(profileId, accountKey, null);
  };

  const switchSession = async (sessionId: string) => {
    if (agentSession?.session_id === sessionId) return;
    if (!profileId) return;
    setError(null);
    await loadSessionById(sessionId, profileId);
  };

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await deleteAgentSession(sessionId);
    } catch {
      /* server may have restarted — still remove locally */
    }
    if (profileId) {
      removeSessionIndex(profileId, accountKey, sessionId);
      setSessionList(loadSessionIndex(profileId, accountKey));
    }
    if (agentSession?.session_id === sessionId) {
      startNewChat();
    }
  };

  const syncMessages = (s: AgentSession) => {
    const msgs = (s.messages ?? []).map((m, i) => ({
      ...m,
      id: `${m.kind ?? m.role}-${i}-${s.session_id}`,
    }));
    setMessages(msgs);
  };

  useEffect(() => {
    if (!profileId && activeProfiles[0]?.id) {
      setProfileId(activeProfiles[0].id);
    }
  }, [activeProfiles, profileId]);

  useEffect(() => {
    if (!profileId) return;
    setAgentSession(null);
    setMessages([]);
    setError(null);
    let cancelled = false;
    refreshSessionList(profileId, accountKey).then(() => {
      if (cancelled) return;
      const activeId = loadActiveSessionId(profileId, accountKey);
      if (activeId) {
        loadSessionById(activeId, profileId);
      } else {
        const entries = loadSessionIndex(profileId, accountKey);
        if (entries[0]) {
          loadSessionById(entries[0].sessionId, profileId);
        }
      }
    });
    return () => {
      cancelled = true;
    };
  }, [profileId, accountKey]);

  useEffect(() => {
    if (!profileId || !agentSession?.session_id) return;
    cacheMessages(agentSession.session_id, messages);
    const firstUser = messages.find((m) => m.role === 'user');
    if (firstUser?.content) {
      syncSessionIndex(agentSession.session_id, firstUser.content, agentSession.status);
    }
  }, [profileId, agentSession?.session_id, agentSession?.status, messages]);

  useEffect(() => {
    const defaultModel = models.find((m) => (m as { default_for_agent?: boolean }).default_for_agent);
    const firstEnabled = models[0];
    const pick = defaultModel ?? firstEnabled;
    if (pick) {
      setModelId(String(pick.id));
    } else {
      setModelId('');
    }
  }, [models]);

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

  const pollSession = async (sessionId: string) => {
    setPolling(true);
    try {
      for (let i = 0; i < 60; i++) {
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
        dry_run: dryRunMode,
        model_id: modelId || undefined,
      });
    },
    onSuccess: async (s, prompt) => {
      setAgentSession(s);
      setError(null);
      saveActiveSessionId(profileId, accountKey, s.session_id);
      syncSessionIndex(s.session_id, String(prompt || 'New chat'), s.status);
      const full = await getAgentSession(s.session_id);
      setAgentSession(full);
      syncMessages(full);
      if (full.status !== 'idle' && full.status !== 'completed') {
        await pollSession(s.session_id);
      }
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Session failed'),
  });

  const planBlocked = Boolean(agentSession?.migration_plan?.blocked);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, messagePending, polling, startSession.isPending, formPending]);

  const handleAgentResponse = async (s: AgentSession) => {
    setAgentSession(s);
    syncMessages(s);
    setError(null);
    const active = ['planning', 'executing', 'validating', 'running'].includes(s.status ?? '');
    const needsPoll =
      active ||
      (s.run_status != null && !['completed', 'failed'].includes(String(s.run_status)));
    if (needsPoll) {
      await pollSession(s.session_id);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: 'user', content: text, kind: 'message' },
    ]);
    if (!agentSession) {
      startSession.mutate(text);
      return;
    }
    setMessagePending(true);
    try {
      const s = await postAgentMessage(agentSession.session_id, text);
      await handleAgentResponse(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Message failed');
    } finally {
      setMessagePending(false);
    }
  };

  const submitForm = async (values: Record<string, unknown>) => {
    if (!agentSession) return;
    setFormPending(true);
    try {
      const s = await submitAgentForm(agentSession.session_id, values);
      await handleAgentResponse(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Form submit failed');
    } finally {
      setFormPending(false);
    }
  };

  const cancelForm = async () => {
    if (!agentSession) return;
    setFormPending(true);
    try {
      const s = await cancelAgentForm(agentSession.session_id);
      setAgentSession(s);
      syncMessages(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Form cancel failed');
    } finally {
      setFormPending(false);
    }
  };

  const agentUnreachable = agentHealthError;
  const llmUnconfigured =
    !agentUnreachable &&
    (Boolean(health?.llm_unconfigured) ||
      Boolean(agentSession?.llm_unconfigured) ||
      noModelsConfigured);
  const llmDegraded =
    !agentUnreachable &&
    !llmUnconfigured &&
    (Boolean(health?.llm_degraded) || Boolean(agentSession?.llm_degraded));

  const renderVisibleMessage = (msg: ChatMessage) => {
    if (msg.kind === 'form') return null;
    const bubbleRole = msg.role === 'user' ? 'user' : 'assistant';
    return (
      <div className={`agent-chat-bubble agent-chat-${bubbleRole}`}>
        {msg.role !== 'user' && msg.subagent ? (
          <div className="agent-chat-meta">
            <span>{msg.subagent}</span>
          </div>
        ) : null}
        {bubbleRole === 'assistant' ? (
          <MarkdownMessage content={msg.content} />
        ) : (
          <p>{msg.content}</p>
        )}
      </div>
    );
  };

  const renderMessageRows = () => {
    const rows: ReactNode[] = [];
    let i = 0;
    while (i < messages.length) {
      const msg = messages[i];
      const isUser = msg.role === 'user';
      const isFinalAssistant =
        !isUser &&
        !isInternalMessage(msg) &&
        (msg.kind === 'message' || msg.kind === undefined) &&
        msg.role !== 'tool';

      if (isUser || isFinalAssistant) {
        rows.push(
          <div key={msg.id} className="agent-message-row">
            {renderVisibleMessage(msg)}
          </div>,
        );
        i += 1;
        continue;
      }

      if (isInternalMessage(msg)) {
        const batch: ChatMessage[] = [];
        while (i < messages.length && isInternalMessage(messages[i])) {
          batch.push(messages[i]);
          i += 1;
        }
        rows.push(
          <div key={batch[0].id} className="agent-message-row">
            <AgentWorkBlock messages={batch} />
          </div>,
        );
        continue;
      }

      rows.push(
        <div key={msg.id} className="agent-message-row">
          {renderVisibleMessage(msg)}
        </div>,
      );
      i += 1;
    }
    return rows;
  };

  return (
    <div className="agent-chat-layout">
      <aside className="agent-session-sidebar" aria-label="Chat sessions">
        <div className="agent-session-sidebar-header">
          <span className="agent-session-sidebar-title">Chats</span>
          <button
            type="button"
            className="oai-button oai-button-secondary agent-session-new"
            onClick={startNewChat}
          >
            New chat
          </button>
        </div>
        <div className="agent-session-list">
          {sessionList.length === 0 ? (
            <p className="form-hint agent-session-empty">No previous chats for this profile.</p>
          ) : (
            sessionList.map((entry) => {
              const active = agentSession?.session_id === entry.sessionId;
              const when = entry.updatedAt
                ? new Date(entry.updatedAt).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : '';
              return (
                <div
                  key={entry.sessionId}
                  className={`agent-session-item${active ? ' agent-session-item-active' : ''}`}
                >
                  <button
                    type="button"
                    className="agent-session-select"
                    onClick={() => switchSession(entry.sessionId)}
                  >
                    <span className="agent-session-item-title">{entry.title}</span>
                    <span className="agent-session-item-meta">
                      {entry.status ? `${entry.status} · ` : ''}
                      {when}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="agent-session-delete"
                    title="Delete chat"
                    aria-label={`Delete chat ${entry.title}`}
                    onClick={() => handleDeleteSession(entry.sessionId)}
                  >
                    ×
                  </button>
                </div>
              );
            })
          )}
        </div>
      </aside>

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

      {!agentUnreachable && llmUnconfigured ? (
        <div className="oai-card" style={{ marginBottom: 12, borderColor: 'var(--warning)' }}>
          <p>{NO_MODELS_HINT}</p>
          {health?.remediation_steps?.map((step) => (
            <p key={step} className="form-hint">{step}</p>
          ))}
        </div>
      ) : null}

      {!agentUnreachable && !llmUnconfigured && llmDegraded && health?.remediation_steps?.length ? (
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
          onChange={(e) => {
            const nextProfile = e.target.value;
            setProfileId(nextProfile);
            setAgentSession(null);
            setMessages([]);
            setSessionList([]);
            setError(null);
          }}
        >
          {activeProfiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}{p.is_default ? ' (default)' : ''}
            </option>
          ))}
        </select>
        <label htmlFor="agent-exec-mode" className="agent-model-label">Mode</label>
        <select
          id="agent-exec-mode"
          className="oai-input agent-model-select"
          value={dryRunMode ? 'dry-run' : 'live'}
          disabled={
            !canOperateAgent
            || Boolean(agentSession && !['idle', 'completed', 'failed', 'awaiting_approval'].includes(agentSession.status ?? ''))
          }
          onChange={(e) => handleDryRunModeChange(e.target.value === 'dry-run')}
          title={
            canApproveLive
              ? 'Admins and approvers can run live migrations without the approval queue'
              : 'Operators need platform approval for live runs'
          }
        >
          <option value="dry-run">Dry-run</option>
          <option value="live">Live (writes to GitHub)</option>
        </select>
        <label htmlFor="agent-model-select" className="agent-model-label">Model</label>
        <select
          id="agent-model-select"
          className="oai-input agent-model-select"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          disabled={noModelsConfigured}
        >
          {noModelsConfigured ? (
            <option value="">No models configured</option>
          ) : (
            models.map((m) => (
              <option key={String(m.id)} value={String(m.id)}>
                {String(m.display_name)} · {String(m.provider)}
              </option>
            ))
          )}
        </select>
        {session?.user ? (
          <span className="agent-model-hint">
            Signed in as <strong>{session.user.display_name}</strong> ({session.user.role})
          </span>
        ) : null}
      </div>

      {llmUnconfigured && !agentSession && !agentUnreachable && (
        <p className="form-hint" style={{ marginBottom: 8 }}>
          {NO_MODELS_HINT}
        </p>
      )}

      {llmDegraded && !llmUnconfigured && !agentSession && !agentUnreachable && (
        <p className="form-hint" style={{ marginBottom: 8 }}>
          Agent is using offline stub mode — configure a live model for full reasoning.
        </p>
      )}

      {agentSession?.tasks?.length ? (
        <AgentTaskTimeline tasks={agentSession.tasks} />
      ) : null}

      {planBlocked && (
        <p className="form-hint" style={{ padding: '8px 16px' }}>
          {agentSession?.migration_plan?.block_reason}
        </p>
      )}

      {agentSession?.status === 'awaiting_approval' && canApproveLive && (
        <div style={{ marginBottom: 12, display: 'flex', gap: 8, padding: '0 16px' }}>
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
        <p className="form-hint" style={{ padding: '0 16px' }}>
          Live execution pending platform approval
          {agentSession.live_approval_id ? ` (${agentSession.live_approval_id})` : ''}.
        </p>
      )}

      {canOperateAgent && agentSession && agentSession.status !== 'awaiting_approval' && agentSession.status !== 'idle' && !canApproveLive && (
        <button
          type="button"
          className="oai-button oai-button-secondary"
          style={{ margin: '0 16px 8px' }}
          onClick={() => requestAgentLive(agentSession.session_id).then(setAgentSession)}
        >
          Request live execution
        </button>
      )}

      <div className="agent-chat-messages" ref={listRef}>
        {renderMessageRows()}
        {agentSession?.pending_form ? (
          <div
            className={
              agentSession.pending_form.form_id === 'plan_confirmation'
                ? 'agent-message-row agent-form-row agent-form-row--full'
                : 'agent-message-row agent-form-row'
            }
          >
            <div
              className={
                agentSession.pending_form.form_id === 'plan_confirmation'
                  ? 'agent-chat-bubble agent-chat-assistant agent-form-bubble agent-form-bubble--full'
                  : 'agent-chat-bubble agent-chat-assistant agent-form-bubble'
              }
            >
              <AgentFormPanel
                form={agentSession.pending_form}
                busy={formPending || messagePending || polling}
                onSubmit={submitForm}
                onCancel={cancelForm}
                executionPolicy={agentSession.execution_policy}
              />
            </div>
          </div>
        ) : null}
        {(startSession.isPending || messagePending || polling || formPending) && (
          <div className="agent-chat-bubble agent-chat-assistant agent-chat-typing">
            <span className="agent-typing-dots">
              {startSession.isPending || messagePending
                ? 'Thinking…'
                : formPending
                  ? 'Submitting…'
                  : 'Running pipeline…'}
            </span>
          </div>
        )}
      </div>

      {error && <p className="oai-error" style={{ padding: '0 16px' }}>{error}</p>}

      <div className="agent-chat-composer">
        <textarea
          className="oai-input agent-chat-input"
          rows={2}
          placeholder={
            llmUnconfigured
              ? 'Configure an LLM model under Settings → LLM models before chatting…'
              : 'Ask to plan or execute a migration (e.g. “Migrate poc phase live” or “execute dry-run”)…'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={Boolean(agentSession?.pending_form) || llmUnconfigured}
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
          disabled={
            !input.trim() ||
            llmUnconfigured ||
            startSession.isPending ||
            messagePending ||
            polling ||
            formPending ||
            Boolean(agentSession?.pending_form)
          }
          onClick={send}
        >
          Send
        </button>
      </div>
    </div>
    </div>
  );
}
