'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { fetchSettings } from '@/lib/api';
import { fetchSession } from '@/lib/auth';
import { canApproveLiveExecution, canOperate } from '@/lib/permissions';
import {
  approveAgentSession,
  AGENT,
  cancelAgentForm,
  cancelAgentSession,
  createAgentSession,
  deleteAgentSession,
  fetchAgentHealth,
  fetchLlmModels,
  formatAgentModelLabel,
  getAgentSession,
  listAgentSessions,
  postAgentMessage,
  requestAgentLive,
  streamAgentMessage,
  streamAgentFormSubmit,
  submitAgentForm,
  type AgentMessage,
  type AgentPendingForm,
  type AgentSession,
  type AgentSessionSummary,
  type AgentTask,
  type StreamEvent,
} from '@/lib/agent';
import {
  chatCacheKey,
  loadActiveSessionId,
  loadAllTurnThinking,
  loadCachedThinking,
  loadSessionIndex,
  mergeChatMessagesForLoad,
  mergeSessionLists,
  removeSessionIndex,
  saveActiveSessionId,
  patchSessionIndex,
  saveSessionIndex,
  saveTurnThinking,
  thinkingCacheKey,
  truncateTitle,
  upsertSessionIndex,
  type SessionIndexEntry,
} from '@/lib/agentSessions';
import { MarkdownMessage } from '@/components/MarkdownMessage';

type ChatMessage = AgentMessage & { id: string };

type SessionSnapshot = {
  agentSession: AgentSession | null;
  messages: ChatMessage[];
  liveThinking: StreamEvent[];
  archivedThinking: Record<number, StreamEvent[]>;
  streaming: boolean;
  messagePending: boolean;
  polling: boolean;
};

function mapSessionMessages(s: AgentSession): ChatMessage[] {
  return (s.messages ?? []).map((m, i) => ({
    ...m,
    id: `${m.kind ?? m.role}-${i}-${s.session_id}`,
  }));
}

function sessionIsBusy(s: AgentSession | null | undefined): boolean {
  if (!s) return false;
  if (ACTIVE_AGENT_STATUSES.has(s.status ?? '')) return true;
  return Boolean(
    s.pipeline_run_id &&
      !['completed', 'failed', 'cancelled', 'idle'].includes(String(s.status ?? '')),
  );
}

const ACTIVE_AGENT_STATUSES = new Set([
  'thinking',
  'planning',
  'executing',
  'validating',
]);

function mergeThinkingEvents(server: StreamEvent[], cached: StreamEvent[]): StreamEvent[] {
  if (!cached.length) return server;
  if (!server.length) return cached;
  return cached.length >= server.length ? cached : server;
}

function thinkingEventsFromSession(s: AgentSession): StreamEvent[] {
  const source = s.thinking_log ?? [];
  return source.map((m) => ({
    kind: m.kind as StreamEvent['kind'],
    content: m.content,
    subagent: (m.subagent as StreamEvent['subagent']) || 'orchestrator',
    meta: m.meta,
    timestamp: m.timestamp,
  }));
}

function appendThinkingEvent(prev: StreamEvent[], evt: StreamEvent): StreamEvent[] {
  const last = prev[prev.length - 1];
  if (
    last &&
    last.kind === evt.kind &&
    last.content === evt.content &&
    last.subagent === evt.subagent
  ) {
    return prev;
  }
  return [...prev, evt];
}

function resetThinkingForSession(
  profileId: string,
  sessionId: string,
  accountKey: string,
  setLiveThinking: (events: StreamEvent[]) => void,
  setThinkingComplete: (complete: boolean) => void,
) {
  setLiveThinking([]);
  setThinkingComplete(false);
  localStorage.removeItem(thinkingCacheKey(profileId, sessionId, accountKey));
}

/** Apply a live SSE event to the per-session snapshot (messages, form, thinking). */
function applyStreamEventToSnapshot(
  sessionId: string,
  evt: StreamEvent,
  handlers: {
    appendThinking: (sessionId: string, evt: StreamEvent) => void;
    setAgentSession: Dispatch<SetStateAction<AgentSession | null>>;
    setMessages: Dispatch<React.SetStateAction<ChatMessage[]>>;
    messagesRef: MutableRefObject<ChatMessage[]>;
    chatFocusRef: MutableRefObject<string | null>;
    sessionSnapshotsRef: MutableRefObject<Map<string, SessionSnapshot>>;
  },
) {
  if (evt.__done__) return;

  if (evt.kind === 'status') {
    const prev = handlers.sessionSnapshotsRef.current.get(sessionId);
    if (prev?.agentSession) {
      const updated = {
        ...prev.agentSession,
        status: evt.content as AgentSession['status'],
      };
      handlers.sessionSnapshotsRef.current.set(sessionId, { ...prev, agentSession: updated });
      if (handlers.chatFocusRef.current === sessionId) {
        handlers.setAgentSession(updated);
      }
    }
    return;
  }

  if (
    evt.kind === 'thinking' ||
    evt.kind === 'progress' ||
    evt.kind === 'tool_call' ||
    evt.kind === 'tool_result' ||
    evt.kind === 'task_update'
  ) {
    handlers.appendThinking(sessionId, evt);
    return;
  }

  if (evt.kind === 'message' && evt.content) {
    const streamed: ChatMessage = {
      id: `a-stream-${sessionId}-${Date.now()}`,
      role: 'assistant',
      content: String(evt.content),
      kind: 'message',
      subagent: evt.subagent,
    };
    const prev = handlers.sessionSnapshotsRef.current.get(sessionId);
    const nextMessages = [...(prev?.messages ?? handlers.messagesRef.current), streamed];
    handlers.sessionSnapshotsRef.current.set(sessionId, {
      ...(prev ?? {
        agentSession: null,
        liveThinking: [],
        archivedThinking: {},
        streaming: true,
        messagePending: false,
        polling: false,
      }),
      messages: nextMessages,
    });
    if (handlers.chatFocusRef.current === sessionId) {
      handlers.setMessages(nextMessages);
    }
    return;
  }

  if (evt.kind === 'form_request' && evt.meta) {
    const form = evt.meta as AgentPendingForm;
    const prev = handlers.sessionSnapshotsRef.current.get(sessionId);
    const updatedSession = prev?.agentSession
      ? { ...prev.agentSession, pending_form: form }
      : ({ session_id: sessionId, status: 'idle', pending_form: form } as AgentSession);
    handlers.sessionSnapshotsRef.current.set(sessionId, {
      ...(prev ?? {
        agentSession: null,
        liveThinking: [],
        archivedThinking: {},
        streaming: true,
        messagePending: false,
        polling: false,
        messages: handlers.messagesRef.current,
      }),
      agentSession: updatedSession,
    });
    if (handlers.chatFocusRef.current === sessionId) {
      handlers.setAgentSession(updatedSession);
    }
  }
}

function isAgentInterruptible(
  session: AgentSession | null,
  flags: {
    streaming?: boolean;
    messagePending?: boolean;
    polling?: boolean;
    formPending?: boolean;
  } = {},
): boolean {
  if (!session || session.pending_form) return false;
  if (flags.streaming || flags.messagePending || flags.polling || flags.formPending) return true;
  if (ACTIVE_AGENT_STATUSES.has(session.status)) return true;
  return Boolean(
    session.pipeline_run_id &&
      !['completed', 'failed', 'cancelled', 'idle'].includes(session.status),
  );
}

/** Normalize user message text for optimistic/server deduplication. */
function normalizeUserMessageKey(content: string): string {
  return (content ?? '')
    .trim()
    .replace(/\bTrue\b/g, 'true')
    .replace(/\bFalse\b/g, 'false');
}

/** Drop optimistic user bubbles once the server persisted the same text. */
function pendingOptimisticUserMessages(
  local: ChatMessage[],
  session: ChatMessage[],
): ChatMessage[] {
  const paired = new Map<string, number>();
  for (const message of session) {
    if (message.role !== 'user') continue;
    const key = normalizeUserMessageKey(message.content ?? '');
    paired.set(key, (paired.get(key) ?? 0) + 1);
  }
  const pending: ChatMessage[] = [];
  for (const message of local) {
    if (message.role !== 'user') continue;
    const key = normalizeUserMessageKey(message.content ?? '');
    const count = paired.get(key) ?? 0;
    if (count > 0) {
      paired.set(key, count - 1);
      continue;
    }
    pending.push(message);
  }
  return pending;
}

const NO_MODELS_HINT =
  'No LLM models are configured. Add, validate, and enable a model under Settings → LLM models.';

function formatFormSubmissionSummary(
  values: Record<string, unknown>,
  formId?: string,
): string {
  if (formId === 'intake_plan_review' || formId === 'plan_confirmation') {
    const confirmed = Boolean(values.plan_confirmed);
    const execute = Boolean(values.confirm_execute);
    const notes = String(values.plan_notes ?? '').trim();
    if (confirmed) {
      const parts = ['Plan confirmed'];
      if (execute) parts.push('start migration');
      return parts.join(', ');
    }
    if (notes) {
      return `Requested plan changes: ${notes}`;
    }
    return 'Plan review submitted';
  }
  const parts: string[] = [];
  for (const [key, value] of Object.entries(values)) {
    if ((value === null || value === undefined || value === '') && key !== 'dry_run') continue;
    if (key === 'dry_run') {
      if (typeof value === 'boolean') {
        parts.push(`dry_run: ${String(value).toLowerCase()}`);
        continue;
      }
      const raw = String(value ?? '').toLowerCase();
      if (raw === 'live' || raw === 'false' || raw === '0') {
        parts.push('dry_run: false');
      } else if (raw === 'dry-run' || raw === 'dryrun' || raw === 'true' || raw === '1') {
        parts.push('dry_run: true');
      }
      continue;
    }
    if (value === false) continue;
    if (typeof value === 'boolean') {
      parts.push(`${key}: ${String(value).toLowerCase()}`);
      continue;
    }
    parts.push(`${key}: ${value}`);
  }
  return parts.join(', ');
}

const THINKING_WORDS = [
  'Thinking',
  'Reasoning',
  'Pondering',
  'Considering',
  'Analyzing',
  'Reflecting',
  'Deliberating',
  'Processing',
  'Formulating',
  'Contemplating',
];

function TypingIndicator({
  active,
  status,
}: {
  active: boolean;
  status?: string;
}) {
  const [wordIdx, setWordIdx] = useState(0);

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      setWordIdx((i) => (i + 1) % THINKING_WORDS.length);
    }, 2000);
    return () => window.clearInterval(id);
  }, [active]);

  if (!active) return null;

  const label =
    status === 'planning'
      ? 'Planning migration'
      : status === 'executing'
        ? 'Executing migration'
        : status === 'validating'
          ? 'Validating migration'
          : THINKING_WORDS[wordIdx];

  return (
    <div className="agent-chat-bubble agent-chat-assistant agent-chat-typing">
      <span className="agent-typing-dots" aria-hidden>
        <span className="agent-typing-dot" />
        <span className="agent-typing-dot" />
        <span className="agent-typing-dot" />
      </span>
      <span className="agent-typing-label">{label}</span>
    </div>
  );
}

const THINKING_KIND_LABEL: Record<string, string> = {
  thinking: 'thinking',
  status: 'status',
  progress: 'progress',
  tool_call: 'tool',
  tool_result: 'result',
  task_update: 'task',
};

const SUBAGENT_LABEL: Record<string, string> = {
  orchestrator: 'AGENT',
  planner: 'PLANNER',
  executor: 'EXECUTOR',
  validator: 'VALIDATOR',
};

function ThinkingRow({ evt }: { evt: StreamEvent }) {
  const subagent = evt.subagent ?? 'orchestrator';
  // For thinking events, use the subagent label (AGENT/PLANNER/EXECUTOR/VALIDATOR)
  // For other events (tool, status, etc.), keep the kind-based label
  const label = evt.kind === 'thinking'
    ? (SUBAGENT_LABEL[subagent] ?? 'AGENT')
    : (THINKING_KIND_LABEL[evt.kind ?? ''] ?? evt.kind);
  return (
    <div className={`agent-thinking-row agent-thinking-row--${evt.kind}`}>
      <span className="agent-thinking-kind">{label}</span>
      <span className="agent-thinking-text">{evt.content}</span>
    </div>
  );
}

function LiveThinkingBlock({ events, done, onAllComplete }: { events: StreamEvent[]; done: boolean; onAllComplete: (complete: boolean) => void }) {
  // Only show parsed events — not raw token fragments (those are LLM JSON output, not human-readable)
  const allEvents = events.filter(
    (e) => e.kind === 'thinking' || e.kind === 'status' || e.kind === 'progress' || e.kind === 'tool_call' || e.kind === 'tool_result' || e.kind === 'task_update',
  );

  // Notify parent when streaming is done
  useEffect(() => {
    if (done) {
      onAllComplete(true);
    }
  }, [done, onAllComplete]);

  const hasEvents = allEvents.length > 0;

  return (
    <div className="agent-message-row" style={{ display: hasEvents ? 'block' : 'none' }}>
      {hasEvents && (
        <details className={`agent-thinking-block${done ? ' agent-thinking-block--done' : ''}`} open={true}>
          <summary className="agent-thinking-header">
            <span className="agent-thinking-chevron">▸</span>
            <span className="agent-thinking-title">
              {done ? 'Agent thoughts' : 'Thinking'}
            </span>
          </summary>
          <div className="agent-thinking-items">
            {allEvents.map((evt, idx) => (
              <ThinkingRow
                key={`${evt.kind}-${evt.content}-${idx}`}
                evt={evt}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

const TASK_STATUS_ICON: Record<string, string> = {
  pending: '○',
  running: '◐',
  completed: '●',
  failed: '✕',
  blocked: '⊘',
  skipped: '—',
  ready: '○',
  'dry-run': '◇',
};

function AgentTaskTimeline({ tasks }: { tasks: AgentTask[] }) {
  if (!tasks.length) return null;
  const done = tasks.filter((t) => t.status === 'completed').length;
  const hasActive = tasks.some((t) => t.status === 'running' || t.status === 'failed');
  return (
    <details className="agent-task-block" open={hasActive}>
      <summary className="agent-task-header">
        <span className="agent-task-chevron">▸</span>
        <span className="agent-task-title">Agent steps</span>
        <span className="agent-task-count">{done}/{tasks.length}</span>
      </summary>
      <div className="agent-task-items">
        {tasks.map((task, idx) => (
          <div key={task.id} className={`agent-task-row agent-task-row--${task.status}`}>
            <span className="agent-task-icon">{TASK_STATUS_ICON[task.status] ?? '○'}</span>
            <span className="agent-task-num">{idx + 1}</span>
            <span className="agent-task-text">{task.label}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

function isStatusMessage(msg: ChatMessage): boolean {
  return msg.kind === 'status';
}

function isInternalMessage(msg: ChatMessage): boolean {
  if (msg.role === 'user') return false;
  if (msg.kind === 'message' || msg.kind === 'form') {
    return false;
  }
  return (
    msg.kind === 'thinking' ||
    msg.kind === 'status' ||
    msg.kind === 'tool_call' ||
    msg.kind === 'tool_result' ||
    msg.kind === 'task_update' ||
    msg.kind === 'progress' ||
    msg.role === 'tool'
  );
}

function formOptionValue(opt: string | { value: string; label: string }): string {
  return typeof opt === 'string' ? opt : opt.value;
}

function formOptionLabel(opt: string | { value: string; label: string }): string {
  return typeof opt === 'string' ? opt : opt.label;
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
        initial[field.name] = formOptionValue(field.options[0]);
      } else if (field.type === 'checkbox') {
        initial[field.name] = field.name === 'confirm_execute';
      }
    }
    setValues(initial);
    setLocalError(null);
  }, [form.form_id, form.fields]);

  const handleSubmit = () => {
    if (form.form_id === 'plan_confirmation' || form.form_id === 'intake_plan_review') {
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
        <label key={field.name} className={`agent-form-field${field.type === 'checkbox' ? ' agent-form-field--checkbox' : ''}`}>
          {field.type === 'checkbox' ? (
            <>
              <input
                type="checkbox"
                disabled={executionPolicy?.requires_live_approval && field.name === 'confirm_execute'}
                checked={Boolean(values[field.name])}
                onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.checked }))}
              />
              <span>{field.label}</span>
            </>
          ) : (
            <>
              <span>{field.label}</span>
              {field.type === 'select' ? (
                <select
                  className="oai-input"
                  value={String(values[field.name] ?? '')}
                  onChange={(e) => setValues((v) => ({ ...v, [field.name]: e.target.value }))}
                >
                  <option value="">Select…</option>
                  {(field.options ?? []).map((opt) => (
                    <option key={formOptionValue(opt)} value={formOptionValue(opt)}>
                      {formOptionLabel(opt)}
                    </option>
                  ))}
                </select>
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
            </>
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
  const models = llmData?.models ?? [];
  const noModelsConfigured = !modelsError && models.length === 0;
  const canApproveLive = canApproveLiveExecution(session?.permissions);
  const canOperateAgent = canOperate(session?.permissions) !== false;
  const accountKey = session?.user?.username ?? 'anonymous';

  const [profileId, setProfileId] = useState('');
  const [modelId, setModelId] = useState('');
  const [agentSession, setAgentSession] = useState<AgentSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [messagePending, setMessagePending] = useState(false);
  const [formPending, setFormPending] = useState(false);
  const [sessionList, setSessionList] = useState<SessionIndexEntry[]>([]);
  const [liveThinking, setLiveThinking] = useState<StreamEvent[]>([]);
  const [archivedThinking, setArchivedThinking] = useState<Record<number, StreamEvent[]>>({});
  const [streaming, setStreaming] = useState(false);
  const [thinkingComplete, setThinkingComplete] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const SCROLL_STICK_THRESHOLD_PX = 80;
  const streamAbortRef = useRef<AbortController | null>(null);
  /** Skip one cache write after clearing messages during session navigation. */
  const suppressMessageCacheRef = useRef(false);
  const messagesRef = useRef<ChatMessage[]>([]);
  const liveThinkingRef = useRef<StreamEvent[]>([]);
  const archivedThinkingRef = useRef<Record<number, StreamEvent[]>>({});
  /** When `'new'`, ignore stale poll/stream updates from a previous session. */
  const chatFocusRef = useRef<string | 'new'>('new');
  /** Session id that `messages` state belongs to — prevents cross-chat merges on switch. */
  const messagesSessionRef = useRef<string | null>(null);
  /** Session id that `liveThinking` belongs to — prevents cross-chat thought bleed. */
  const thinkingSessionRef = useRef<string | null>(null);
  /** Set when operator clicks New chat — skip auto-loading the most recent session. */
  const preferBlankChatRef = useRef(false);
  /** In-memory per-session UI state — supports switching while agents run in background. */
  const sessionSnapshotsRef = useRef<Map<string, SessionSnapshot>>(new Map());

  const registerSessionInList = (sessionId: string, title: string, status?: string) => {
    if (!profileId) return;
    upsertSessionIndex(profileId, accountKey, {
      sessionId,
      title: truncateTitle(title),
      updatedAt: new Date().toISOString(),
      status,
    });
    setSessionList(loadSessionIndex(profileId, accountKey));
  };

  const patchSessionInList = (
    sessionId: string,
    patch: { title?: string; status?: string },
  ) => {
    if (!profileId) return;
    setSessionList((prev) => {
      const idx = prev.findIndex((entry) => entry.sessionId === sessionId);
      if (idx < 0) return prev;
      const current = prev[idx];
      const nextEntry = { ...current, ...patch };
      if (
        nextEntry.status === current.status &&
        (patch.title === undefined || nextEntry.title === current.title)
      ) {
        return prev;
      }
      const next = [...prev];
      next[idx] = nextEntry;
      patchSessionIndex(profileId, accountKey, sessionId, patch);
      return next;
    });
  };

  const handleMessagesScroll = () => {
    const el = listRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom <= SCROLL_STICK_THRESHOLD_PX;
  };

  const cacheMessages = (sessionId: string, msgs: ChatMessage[]) => {
    if (!profileId || !sessionId) return;
    localStorage.setItem(chatCacheKey(profileId, sessionId, accountKey), JSON.stringify(msgs));
  };

  const cacheThinking = (sessionId: string, events: StreamEvent[]) => {
    if (!profileId || !sessionId || events.length === 0) return;
    localStorage.setItem(thinkingCacheKey(profileId, sessionId, accountKey), JSON.stringify(events));
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

  const snapshotCurrentSessionToCache = () => {
    const sid = messagesSessionRef.current;
    if (!profileId || !sid) return;
    const msgs = messagesRef.current;
    if (msgs.length > 0) {
      cacheMessages(sid, msgs);
    }
    const thinking = liveThinkingRef.current;
    if (thinking.length > 0) {
      cacheThinking(sid, thinking);
    }
  };

  const loadArchivedThinkingForSession = (s: AgentSession): Record<number, StreamEvent[]> => {
    if (!profileId || !s.session_id) return {};
    const userCount = (s.messages ?? []).filter((m) => m.role === 'user').length;
    const raw = loadAllTurnThinking(profileId, s.session_id, accountKey, userCount);
    const mapped: Record<number, StreamEvent[]> = {};
    for (const [key, value] of Object.entries(raw)) {
      if (Array.isArray(value) && value.length) {
        mapped[Number(key)] = value as StreamEvent[];
      }
    }
    return mapped;
  };

  const buildMessagesFromSession = (
    s: AgentSession,
    prevMessages?: ChatMessage[] | null,
  ): ChatMessage[] => {
    const cached = prevMessages ?? loadCachedMessages(s.session_id);
    const merged = mergeChatMessagesForLoad(s.messages ?? [], cached, s.session_id);
    return merged.map((m, i) => ({
      ...m,
      id: m.id ?? `${m.kind ?? m.role}-${i}-${s.session_id}`,
      role: (m.role ?? 'assistant') as AgentMessage['role'],
      content: m.content ?? '',
    })) as ChatMessage[];
  };

  const applySnapshotToUi = (sessionId: string, snapshot: SessionSnapshot) => {
    messagesSessionRef.current = sessionId;
    thinkingSessionRef.current = sessionId;
    setAgentSession(snapshot.agentSession);
    setMessages(snapshot.messages);
    setLiveThinking(snapshot.liveThinking);
    setArchivedThinking(snapshot.archivedThinking);
    setStreaming(snapshot.streaming);
    setMessagePending(snapshot.messagePending);
    setPolling(snapshot.polling);
    setThinkingComplete(
      Boolean(snapshot.agentSession?.pending_form) ||
        (!snapshot.streaming &&
          !snapshot.messagePending &&
          !snapshot.polling &&
          !sessionIsBusy(snapshot.agentSession)),
    );
  };

  const persistFocusedSessionSnapshot = () => {
    const sid = agentSession?.session_id ?? messagesSessionRef.current;
    if (!sid) return;
    const snapshot: SessionSnapshot = {
      agentSession,
      messages: messagesRef.current,
      liveThinking: liveThinkingRef.current,
      archivedThinking: archivedThinkingRef.current,
      streaming,
      messagePending,
      polling,
    };
    sessionSnapshotsRef.current.set(sid, snapshot);
    snapshotCurrentSessionToCache();
  };

  const appendThinkingToSnapshot = (sessionId: string, evt: StreamEvent) => {
    const prev =
      sessionSnapshotsRef.current.get(sessionId) ??
      ({
        agentSession: null,
        messages: messagesRef.current,
        liveThinking: [],
        archivedThinking: {},
        streaming: true,
        messagePending: true,
        polling: false,
      } satisfies SessionSnapshot);
    const nextThinking = appendThinkingEvent(prev.liveThinking, evt);
    sessionSnapshotsRef.current.set(sessionId, { ...prev, liveThinking: nextThinking });
    if (profileId && nextThinking.length > 0) {
      cacheThinking(sessionId, nextThinking);
    }
    if (chatFocusRef.current === sessionId && thinkingSessionRef.current === sessionId) {
      setLiveThinking(nextThinking);
    }
  };

  const mergeServerIntoSnapshot = (sessionId: string, s: AgentSession) => {
    const prev = sessionSnapshotsRef.current.get(sessionId);
    const sessionMsgs = buildMessagesFromSession(s, prev?.messages);
    const fromServer = thinkingEventsFromSession(s);
    const cachedThinking = profileId
      ? (loadCachedThinking(profileId, sessionId, accountKey) as StreamEvent[])
      : [];
    const liveThinkingNext =
      fromServer.length > 0
        ? fromServer
        : prev?.liveThinking?.length
          ? prev.liveThinking
          : cachedThinking;
    const archived = loadArchivedThinkingForSession(s);
    const busy = sessionIsBusy(s);
    const snapshot: SessionSnapshot = {
      agentSession: s,
      messages: sessionMsgs,
      liveThinking: liveThinkingNext,
      archivedThinking: archived,
      streaming: busy ? (prev?.streaming ?? false) : false,
      messagePending: busy ? (prev?.messagePending ?? false) : false,
      polling: prev?.polling ?? false,
    };
    sessionSnapshotsRef.current.set(sessionId, snapshot);
    if (profileId) {
      cacheMessages(sessionId, sessionMsgs);
      if (liveThinkingNext.length > 0) {
        cacheThinking(sessionId, liveThinkingNext);
      }
    }
    const firstUser = sessionMsgs.find((m) => m.role === 'user');
    if (firstUser?.content) {
      patchSessionInList(sessionId, { status: s.status });
    } else if (s.status) {
      patchSessionInList(sessionId, { status: s.status });
    }
    if (chatFocusRef.current === sessionId) {
      applySnapshotToUi(sessionId, snapshot);
    }
    return snapshot;
  };

  const applySessionThinking = (s: AgentSession) => {
    if (chatFocusRef.current === s.session_id) {
      hydrateThinkingFromSession(s);
    }
    const prev = sessionSnapshotsRef.current.get(s.session_id);
    const fromServer = thinkingEventsFromSession(s);
    if (prev) {
      sessionSnapshotsRef.current.set(s.session_id, {
        ...prev,
        agentSession: s,
        liveThinking: fromServer.length ? fromServer : prev.liveThinking,
      });
    }
  };

  const finalizeStreamForSession = (
    sessionId: string,
    s: AgentSession,
    turnEvents: StreamEvent[],
  ) => {
    const turnIndex = (s.messages ?? []).filter((m) => m.role === 'user').length - 1;
    if (profileId && turnEvents.length > 0 && turnIndex >= 0) {
      saveTurnThinking(profileId, sessionId, accountKey, turnIndex, turnEvents);
    }
    const archived = loadArchivedThinkingForSession(s);
    if (turnIndex >= 0 && turnEvents.length > 0) {
      archived[turnIndex] = turnEvents;
    }
    mergeServerIntoSnapshot(sessionId, s);
    const prev = sessionSnapshotsRef.current.get(sessionId);
    if (prev) {
      const done: SessionSnapshot = {
        ...prev,
        streaming: false,
        messagePending: false,
        archivedThinking: archived,
      };
      sessionSnapshotsRef.current.set(sessionId, done);
      if (chatFocusRef.current === sessionId) {
        applySnapshotToUi(sessionId, done);
        setThinkingComplete(true);
      }
    }
  };

  const hydrateMessagesFromSession = (
    s: AgentSession,
    options?: { replace?: boolean },
  ) => {
    const cached = loadCachedMessages(s.session_id);
    const merged = mergeChatMessagesForLoad(s.messages ?? [], cached, s.session_id);
    const sessionMsgs = merged.map((m, i) => ({
      ...m,
      id: m.id ?? `${m.kind ?? m.role}-${i}-${s.session_id}`,
      role: (m.role ?? 'assistant') as AgentMessage['role'],
      content: m.content ?? '',
    })) as ChatMessage[];

    setMessages((prev) => {
      const sameSession = !options?.replace && messagesSessionRef.current === s.session_id;
      const relevant = sameSession ? prev : [];
      const pending = pendingOptimisticUserMessages(relevant, sessionMsgs);
      messagesSessionRef.current = s.session_id;
      return [...sessionMsgs, ...pending];
    });
  };

  const hydrateThinkingFromSession = (s: AgentSession) => {
    if (thinkingSessionRef.current && thinkingSessionRef.current !== s.session_id) {
      return;
    }
    const fromServer = thinkingEventsFromSession(s);
    const fromCache = (profileId
      ? loadCachedThinking(profileId, s.session_id, accountKey)
      : []) as StreamEvent[];
    setLiveThinking(mergeThinkingEvents(fromServer, fromCache));
    setThinkingComplete(!ACTIVE_AGENT_STATUSES.has(s.status));
  };

  const archiveThinkingForTurn = (
    sessionId: string,
    turnIndex: number,
    events: StreamEvent[],
  ) => {
    if (!profileId || !events.length || turnIndex < 0) return;
    saveTurnThinking(profileId, sessionId, accountKey, turnIndex, events);
    setArchivedThinking((prev) => ({ ...prev, [turnIndex]: events }));
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
      saveSessionIndex(pid, userKey, merged);
      setSessionList(merged);
    } catch {
      setSessionList(local);
    }
  };

  const loadSessionById = async (sessionId: string, expectedProfileId: string) => {
    const known = loadSessionIndex(expectedProfileId, accountKey).some(
      (entry) => entry.sessionId === sessionId,
    );
    chatFocusRef.current = sessionId;
    preferBlankChatRef.current = false;
    setFormPending(false);

    const snapshot = sessionSnapshotsRef.current.get(sessionId);
    const cached = loadCachedMessages(sessionId);
    if (snapshot) {
      applySnapshotToUi(sessionId, snapshot);
    } else if (cached?.length) {
      const stub: AgentSession = {
        session_id: sessionId,
        status: 'idle',
        messages: cached,
        profile_id: expectedProfileId,
      };
      applySnapshotToUi(sessionId, {
        agentSession: stub,
        messages: cached,
        liveThinking: profileId
          ? (loadCachedThinking(profileId, sessionId, accountKey) as StreamEvent[])
          : [],
        archivedThinking: loadArchivedThinkingForSession(stub),
        streaming: false,
        messagePending: false,
        polling: false,
      });
    } else {
      suppressMessageCacheRef.current = true;
      setAgentSession(null);
      setMessages([]);
      setArchivedThinking({});
      setLiveThinking([]);
      setStreaming(false);
      setMessagePending(false);
      setPolling(false);
    }

    try {
      const s = await getAgentSession(sessionId);
      if (chatFocusRef.current !== sessionId) return false;
      if (s.profile_id && s.profile_id !== expectedProfileId) {
        removeSessionIndex(expectedProfileId, accountKey, sessionId);
        return false;
      }
      mergeServerIntoSnapshot(sessionId, s);
      saveActiveSessionId(expectedProfileId, accountKey, sessionId);
      if (sessionIsBusy(s)) {
        void pollSession(sessionId, { background: false });
      }
      return true;
    } catch {
      if (!known && !snapshot && !cached?.length) return false;
      saveActiveSessionId(expectedProfileId, accountKey, sessionId);
      return true;
    }
  };

  const startNewChat = () => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    snapshotCurrentSessionToCache();
    suppressMessageCacheRef.current = true;
    chatFocusRef.current = 'new';
    messagesSessionRef.current = null;
    thinkingSessionRef.current = null;
    preferBlankChatRef.current = true;
    setAgentSession(null);
    setMessages([]);
    setArchivedThinking({});
    setLiveThinking([]);
    setThinkingComplete(true);
    setStreaming(false);
    setMessagePending(false);
    setPolling(false);
    setFormPending(false);
    setInput('');
    setError(null);
    if (profileId) saveActiveSessionId(profileId, accountKey, null);
  };

  const switchSession = async (sessionId: string) => {
    if (agentSession?.session_id === sessionId) return;
    if (!profileId) return;
    persistFocusedSessionSnapshot();
    streamAbortRef.current = null;
    stickToBottomRef.current = true;
    setError(null);
    await loadSessionById(sessionId, profileId);
  };

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await deleteAgentSession(sessionId);
    } catch {
      /* server may have restarted — still remove locally */
    }
    sessionSnapshotsRef.current.delete(sessionId);
    if (profileId) {
      removeSessionIndex(profileId, accountKey, sessionId);
      setSessionList(loadSessionIndex(profileId, accountKey));
    }
    if (agentSession?.session_id === sessionId) {
      startNewChat();
    }
  };

  const syncMessages = (s: AgentSession, options?: { replace?: boolean }) => {
    hydrateMessagesFromSession(s, options);
  };

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    liveThinkingRef.current = liveThinking;
  }, [liveThinking]);

  useEffect(() => {
    archivedThinkingRef.current = archivedThinking;
  }, [archivedThinking]);

  useEffect(() => {
    const sid = agentSession?.session_id;
    if (!sid || chatFocusRef.current !== sid) return;
    sessionSnapshotsRef.current.set(sid, {
      agentSession,
      messages,
      liveThinking,
      archivedThinking,
      streaming,
      messagePending,
      polling,
    });
  }, [
    agentSession,
    messages,
    liveThinking,
    archivedThinking,
    streaming,
    messagePending,
    polling,
  ]);

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
      } else if (!preferBlankChatRef.current) {
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
    if (messagesSessionRef.current !== agentSession.session_id) return;
    if (suppressMessageCacheRef.current) {
      suppressMessageCacheRef.current = false;
      return;
    }
    cacheMessages(agentSession.session_id, messages);
  }, [profileId, agentSession?.session_id, messages]);

  useEffect(() => {
    if (!profileId || !agentSession?.session_id || !agentSession.status) return;
    patchSessionInList(agentSession.session_id, { status: agentSession.status });
  }, [profileId, agentSession?.session_id, agentSession?.status]);

  useEffect(() => {
    if (!profileId || !agentSession?.session_id) return;
    if (thinkingSessionRef.current !== agentSession.session_id) return;
    if (liveThinking.length > 0) {
      cacheThinking(agentSession.session_id, liveThinking);
    }
  }, [profileId, agentSession?.session_id, liveThinking, accountKey]);

  useEffect(() => {
    if (!profileId) return;
    const id = setInterval(async () => {
      const busyIds = new Set<string>();
      for (const entry of sessionList) {
        if (entry.status && ACTIVE_AGENT_STATUSES.has(entry.status)) {
          busyIds.add(entry.sessionId);
        }
      }
      for (const [sid, snap] of sessionSnapshotsRef.current.entries()) {
        if (
          snap.streaming ||
          snap.messagePending ||
          snap.polling ||
          sessionIsBusy(snap.agentSession)
        ) {
          busyIds.add(sid);
        }
      }
      if (agentSession?.session_id && sessionIsBusy(agentSession)) {
        busyIds.add(agentSession.session_id);
      }
      for (const sessionId of busyIds) {
        if (chatFocusRef.current === sessionId && streaming) continue;
        try {
          const s = await getAgentSession(sessionId);
          mergeServerIntoSnapshot(sessionId, s);
        } catch {
          /* background poll */
        }
      }
    }, 2500);
    return () => clearInterval(id);
  }, [profileId, sessionList, agentSession?.session_id, agentSession?.status, streaming]);

  useEffect(() => {
    if (!agentSession?.session_id) return;
    const shouldPoll =
      streaming ||
      ACTIVE_AGENT_STATUSES.has(agentSession.status) ||
      Boolean(
        agentSession.pipeline_run_id &&
          !['completed', 'failed', 'cancelled', 'idle'].includes(agentSession.status),
      );
    if (!shouldPoll) return;

    const pollSessionId = agentSession.session_id;
    const id = setInterval(async () => {
      if (chatFocusRef.current !== pollSessionId) return;
      try {
        const s = await getAgentSession(pollSessionId);
        mergeServerIntoSnapshot(pollSessionId, s);
      } catch {
        /* ignore poll errors */
      }
    }, 2000);
    return () => clearInterval(id);
  }, [
    agentSession?.session_id,
    agentSession?.status,
    agentSession?.pipeline_run_id,
    streaming,
  ]);

  useEffect(() => {
    const defaultFromApi = llmData?.default_model_id;
    const defaultModel =
      (defaultFromApi ? models.find((m) => String(m.id) === defaultFromApi) : undefined) ??
      models.find((m) => (m as { default_for_agent?: boolean }).default_for_agent);
    const firstEnabled = models[0];
    const pick = defaultModel ?? firstEnabled;
    if (pick) {
      setModelId(String(pick.id));
    } else {
      setModelId('');
    }
  }, [models, llmData?.default_model_id]);

  useEffect(() => {
    if (!agentSession || agentSession.live_approval_status !== 'pending') return;
    const pollSessionId = agentSession.session_id;
    const id = setInterval(async () => {
      if (chatFocusRef.current !== pollSessionId) return;
      try {
        const s = await getAgentSession(pollSessionId);
        if (chatFocusRef.current !== pollSessionId) return;
        setAgentSession(s);
        syncMessages(s);
        if (s.live_approval_status !== 'pending') clearInterval(id);
      } catch {
        /* ignore poll errors */
      }
    }, 10_000);
    return () => clearInterval(id);
  }, [agentSession?.session_id, agentSession?.live_approval_status]);

  const pollSession = async (
    sessionId: string,
    options?: { background?: boolean },
  ) => {
    const pollFor = sessionId;
    const background = options?.background ?? chatFocusRef.current !== pollFor;
    if (!background) setPolling(true);
    const snap = sessionSnapshotsRef.current.get(sessionId);
    if (snap) {
      sessionSnapshotsRef.current.set(sessionId, { ...snap, polling: true });
    }
    try {
      for (let i = 0; i < 120; i++) {
        const s = await getAgentSession(sessionId);
        mergeServerIntoSnapshot(sessionId, s);
        const hasPendingForm = Boolean(s.pending_form);
        const stillActive = ACTIVE_AGENT_STATUSES.has(s.status);
        if (hasPendingForm || !stillActive) break;
        await new Promise((r) => setTimeout(r, 500));
      }
    } finally {
      const done = sessionSnapshotsRef.current.get(sessionId);
      if (done) {
        sessionSnapshotsRef.current.set(sessionId, { ...done, polling: false });
      }
      if (!background && chatFocusRef.current === pollFor) {
        setPolling(false);
      }
    }
  };

  const startSession = useMutation({
    mutationFn: async (prompt: string) => {
      const pid = profileId || activeProfiles[0]?.id || 'lightweight';
      return createAgentSession({
        profile_id: pid,
        prompt,
        model_id: modelId || undefined,
      });
    },
    onSuccess: async (s, prompt) => {
      messagesSessionRef.current = s.session_id;
      thinkingSessionRef.current = s.session_id;
      chatFocusRef.current = s.session_id;
      setAgentSession(s);
      setError(null);
      saveActiveSessionId(profileId, accountKey, s.session_id);
      registerSessionInList(s.session_id, String(prompt || 'New chat'), s.status);
      const full = await getAgentSession(s.session_id);
      if (chatFocusRef.current !== s.session_id) return;
      setAgentSession(full);
      syncMessages(full, { replace: true });
      setArchivedThinking(loadArchivedThinkingForSession(full));
      if (full.status !== 'idle' && full.status !== 'completed') {
        await pollSession(s.session_id);
      }
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Session failed'),
  });

  const planBlocked = Boolean(agentSession?.migration_plan?.blocked);
  const agentInterruptible = isAgentInterruptible(agentSession, {
    streaming,
    messagePending,
    polling,
    formPending,
  });

  useEffect(() => {
    const el = listRef.current;
    if (!el || !stickToBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, messagePending, polling, startSession.isPending, formPending]);

  const handleAgentResponse = async (s: AgentSession) => {
    mergeServerIntoSnapshot(s.session_id, s);
    setError(null);
    const active = ACTIVE_AGENT_STATUSES.has(s.status ?? '');
    const needsPoll =
      active ||
      (s.run_status != null && !['completed', 'failed'].includes(String(s.run_status)));
    if (needsPoll) {
      await pollSession(s.session_id);
    }
  };

  const interruptAgent = async () => {
    if (!agentSession) return;
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setMessagePending(true);
    try {
      await cancelAgentSession(agentSession.session_id, 'stop');
      const s = await getAgentSession(agentSession.session_id);
      setAgentSession(s);
      syncMessages(s);
      hydrateThinkingFromSession(s);
      setThinkingComplete(true);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Cancel failed');
    } finally {
      setStreaming(false);
      setMessagePending(false);
      setPolling(false);
      setFormPending(false);
    }
  };

  const send = async () => {
    const interruptible = isAgentInterruptible(agentSession, {
      streaming,
      messagePending,
      polling,
      formPending,
    });
    if (interruptible) {
      await interruptAgent();
      return;
    }

    const text = input.trim();
    if (!text) return;
    setInput('');

    let sessionId = agentSession?.session_id;
    const isNewSession = !sessionId;

    if (!isNewSession && sessionId) {
      messagesSessionRef.current = sessionId;
      const optimistic: ChatMessage = {
        id: `u-${sessionId}-${Date.now()}`,
        role: 'user',
        content: text,
        kind: 'message',
      };
      setMessages((prev) => [...prev, optimistic]);
      const prevSnap = sessionSnapshotsRef.current.get(sessionId);
      sessionSnapshotsRef.current.set(sessionId, {
        agentSession: prevSnap?.agentSession ?? agentSession,
        messages: [...(prevSnap?.messages ?? messagesRef.current), optimistic],
        liveThinking: [],
        archivedThinking: prevSnap?.archivedThinking ?? archivedThinking,
        streaming: true,
        messagePending: true,
        polling: false,
      });
    }

    if (!sessionId) {
      const pid = profileId || activeProfiles[0]?.id || 'lightweight';
      try {
        const s = await createAgentSession({
          profile_id: pid,
          prompt: '',
          model_id: modelId || undefined,
        });
        sessionId = s.session_id;
        chatFocusRef.current = sessionId;
        messagesSessionRef.current = sessionId;
        thinkingSessionRef.current = sessionId;
        preferBlankChatRef.current = false;
        setAgentSession(s);
        setMessages([]);
        setArchivedThinking({});
        setLiveThinking([]);
        setThinkingComplete(false);
        setError(null);
        saveActiveSessionId(profileId, accountKey, s.session_id);
        registerSessionInList(s.session_id, text, s.status);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Session creation failed');
        return;
      }
    }

    if (!sessionId) return;
    chatFocusRef.current = sessionId;
    preferBlankChatRef.current = false;
    stickToBottomRef.current = true;
    setMessagePending(true);
    setStreaming(true);
    if (profileId) {
      resetThinkingForSession(profileId, sessionId, accountKey, setLiveThinking, setThinkingComplete);
    } else {
      setLiveThinking([]);
      setThinkingComplete(false);
    }
    thinkingSessionRef.current = sessionId;
    try {
      const abort = new AbortController();
      streamAbortRef.current = abort;
      const s = await streamAgentMessage(
        sessionId,
        text,
        (evt) => {
          applyStreamEventToSnapshot(sessionId, evt, {
            appendThinking: appendThinkingToSnapshot,
            setAgentSession,
            setMessages,
            messagesRef,
            chatFocusRef,
            sessionSnapshotsRef,
          });
        },
        abort.signal,
      );
      const turnEvents = thinkingEventsFromSession(s);
      finalizeStreamForSession(sessionId, s, turnEvents);
      if (chatFocusRef.current === sessionId) {
        const needsPoll =
          ACTIVE_AGENT_STATUSES.has(s.status ?? '') ||
          (s.run_status != null && !['completed', 'failed'].includes(String(s.run_status)));
        if (needsPoll) {
          await pollSession(sessionId);
        }
      } else if (sessionIsBusy(s)) {
        void pollSession(sessionId, { background: true });
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') {
        return;
      }
      setError(e instanceof Error ? e.message : 'Message failed');
    } finally {
      streamAbortRef.current = null;
      const snap = sessionSnapshotsRef.current.get(sessionId);
      if (snap) {
        sessionSnapshotsRef.current.set(sessionId, {
          ...snap,
          streaming: false,
          messagePending: false,
        });
      }
      if (chatFocusRef.current === sessionId) {
        setStreaming(false);
        setMessagePending(false);
      }
    }
  };

  const submitForm = async (values: Record<string, unknown>) => {
    if (!agentSession) return;
    setFormPending(true);
    setStreaming(true);
    if (profileId) {
      resetThinkingForSession(
        profileId,
        agentSession.session_id,
        accountKey,
        setLiveThinking,
        setThinkingComplete,
      );
    } else {
      setLiveThinking([]);
      setThinkingComplete(false);
    }
    const formTitle = agentSession.pending_form?.title || 'Form';
    const summary = formatFormSubmissionSummary(values, agentSession.pending_form?.form_id);
    const formSessionId = agentSession.session_id;
    messagesSessionRef.current = formSessionId;
    thinkingSessionRef.current = formSessionId;
    setMessages((prev) => [
      ...prev,
      {
        id: `u-form-${formSessionId}-${Date.now()}`,
        role: 'user',
        content: `${formTitle} submitted — ${summary}`,
        kind: 'message',
      },
    ]);
    try {
      const s = await streamAgentFormSubmit(
        agentSession.session_id,
        values,
        (evt) => {
          applyStreamEventToSnapshot(formSessionId, evt, {
            appendThinking: appendThinkingToSnapshot,
            setAgentSession,
            setMessages,
            messagesRef,
            chatFocusRef,
            sessionSnapshotsRef,
          });
        },
      );
      mergeServerIntoSnapshot(formSessionId, s);
      setError(null);
      await pollSession(s.session_id, {
        background: chatFocusRef.current !== formSessionId,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Form submit failed');
    } finally {
      const snap = sessionSnapshotsRef.current.get(formSessionId);
      if (snap) {
        sessionSnapshotsRef.current.set(formSessionId, {
          ...snap,
          streaming: false,
        });
      }
      if (chatFocusRef.current === formSessionId) {
        setFormPending(false);
        setStreaming(false);
      }
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
    const statusClass = isStatusMessage(msg) ? ' agent-chat-status' : '';

    return (
      <div className={`agent-chat-bubble agent-chat-${bubbleRole}${statusClass}`}>
        {msg.role !== 'user' && !isStatusMessage(msg) && msg.kind === 'status' ? (
          <div className="agent-chat-meta">
            <span>status</span>
          </div>
        ) : null}
        {bubbleRole === 'assistant' ? (
          <TypewriterMessage content={msg.content || ''} streaming={streaming} messageId={msg.id} />
        ) : (
          <p>{msg.content}</p>
        )}
      </div>
    );
  };

  function TypewriterMessage({ content, streaming, messageId }: { content: string; streaming: boolean; messageId: string }) {
    return <MarkdownMessage content={content} />;
  }

  const renderMessageRows = () => {
    const rows: ReactNode[] = [];
    let i = 0;
    let lastUserIdx = -1;
    let userTurnIndex = -1;
    const sessionStillActive = ACTIVE_AGENT_STATUSES.has(agentSession?.status ?? '');
    const thinkingDone = !streaming && !sessionStillActive;
    const showLiveThinking = Boolean(agentSession?.session_id) && (streaming || liveThinking.length > 0);

    for (let j = messages.length - 1; j >= 0; j--) {
      if (messages[j].role === 'user') {
        lastUserIdx = j;
        break;
      }
    }

    while (i < messages.length) {
      const msg = messages[i];
      const isUser = msg.role === 'user';
      const isAssistant = msg.role === 'assistant';

      if (isUser || (isAssistant && msg.content)) {
        rows.push(
          <div key={msg.id} className="agent-message-row">
            {renderVisibleMessage(msg)}
          </div>,
        );

        if (isUser) {
          userTurnIndex += 1;
          const isLastUser = i === lastUserIdx;
          const archived = archivedThinking[userTurnIndex];

          if (isLastUser) {
            rows.push(
              <TypingIndicator
                key="typing-indicator"
                active={streaming || (!streaming && (startSession.isPending || messagePending || polling || formPending))}
                status={formPending ? undefined : agentSession?.status}
              />,
            );
            if (showLiveThinking) {
              rows.push(
                <LiveThinkingBlock
                  key={`live-thinking-${userTurnIndex}`}
                  events={liveThinking}
                  done={thinkingDone}
                  onAllComplete={setThinkingComplete}
                />,
              );
            }
          } else if (archived?.length) {
            rows.push(
              <LiveThinkingBlock
                key={`archived-thinking-${userTurnIndex}`}
                events={archived}
                done={true}
                onAllComplete={() => {}}
              />,
            );
          }
        }

        i += 1;
        continue;
      }

      i += 1;
    }

    if (agentSession?.pending_form && !formPending) {
      rows.push(
        <div
          key="pending-form"
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
      );
    }

    if (lastUserIdx === -1 && (streaming || startSession.isPending || messagePending)) {
      rows.push(
        <TypingIndicator
          key="typing-indicator"
          active={streaming || startSession.isPending || messagePending}
          status={agentSession?.status}
        />,
      );
      if (showLiveThinking) {
        rows.push(
          <LiveThinkingBlock
            key="live-thinking-initial"
            events={liveThinking}
            done={thinkingDone}
            onAllComplete={setThinkingComplete}
          />,
        );
      }
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
              <option key={m.id} value={m.id}>
                {formatAgentModelLabel(m)}
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

      {planBlocked && (
        <p className="form-hint" style={{ padding: '8px 16px' }}>
          {agentSession?.migration_plan?.block_reason}
        </p>
      )}

      {agentSession?.live_approval_status === 'pending' && canApproveLive && (
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

      {agentSession?.live_approval_status === 'pending' && !canApproveLive && (
        <p className="form-hint" style={{ padding: '0 16px' }}>
          Live execution pending platform approval
          {agentSession.live_approval_id ? ` (${agentSession.live_approval_id})` : ''}.
        </p>
      )}

      {canOperateAgent && agentSession && agentSession.live_approval_status !== 'pending' && ACTIVE_AGENT_STATUSES.has(agentSession.status) && !canApproveLive && (
        <button
          type="button"
          className="oai-button oai-button-secondary"
          style={{ margin: '0 16px 8px' }}
          onClick={() => requestAgentLive(agentSession.session_id).then(setAgentSession)}
        >
          Request live execution
        </button>
      )}

      <div
        className="agent-chat-messages"
        ref={listRef}
        onScroll={handleMessagesScroll}
      >
        {renderMessageRows()}
        {agentSession?.tasks?.some((t) => t.status !== 'pending') ? (
          <div className="agent-message-row">
            <AgentTaskTimeline tasks={agentSession.tasks} />
          </div>
        ) : null}
      </div>

      {error && <p className="oai-error" style={{ padding: '0 16px' }}>{error}</p>}

      <div className="agent-chat-composer">
        <textarea
          className="oai-input agent-chat-input"
          rows={2}
          placeholder={
            llmUnconfigured
              ? 'Configure an LLM model under Settings → LLM models before chatting…'
              : agentInterruptible
                ? 'Agent is running — press Cancel to stop…'
                : 'Describe what you want to migrate — e.g. "Migrate Project/RepoName" or "Show migration status"…'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={Boolean(agentSession?.pending_form) || llmUnconfigured || startSession.isPending}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="button"
          className={`oai-button oai-button-primary agent-chat-send${agentInterruptible ? ' agent-chat-cancel' : ''}`}
          disabled={
            agentInterruptible
              ? llmUnconfigured || startSession.isPending
              : !input.trim() ||
                llmUnconfigured ||
                startSession.isPending ||
                messagePending ||
                streaming ||
                polling ||
                formPending ||
                Boolean(agentSession?.pending_form)
          }
          onClick={send}
        >
          {agentInterruptible ? 'Cancel' : 'Send'}
        </button>
      </div>
    </div>
    </div>
  );
}
