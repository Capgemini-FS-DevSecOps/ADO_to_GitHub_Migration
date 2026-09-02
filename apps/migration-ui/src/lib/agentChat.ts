/**
 * Pure helpers behind AgentChat — SSE thinking-stream reduction, optimistic message
 * dedup, and HITL form defaults/summaries. Kept out of the component so they can be
 * tested without a DOM.
 */
import { normalizeUserMessageKey } from './agentSessions';
import type {
  AgentFormField,
  AgentFormFieldOption,
  AgentMessage,
  AgentSession,
  StreamEvent,
} from './agent';

export type ChatMessage = AgentMessage & { id: string };

export const ACTIVE_AGENT_STATUSES = new Set([
  'thinking',
  'planning',
  'executing',
  'validating',
]);

const SETTLED_STATUSES = ['completed', 'failed', 'cancelled', 'idle'];

export function sessionIsBusy(s: AgentSession | null | undefined): boolean {
  if (!s) return false;
  if (ACTIVE_AGENT_STATUSES.has(s.status ?? '')) return true;
  return Boolean(s.pipeline_run_id && !SETTLED_STATUSES.includes(String(s.status ?? '')));
}

export function isAgentInterruptible(
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
  return Boolean(session.pipeline_run_id && !SETTLED_STATUSES.includes(session.status));
}

export function mergeThinkingEvents(server: StreamEvent[], cached: StreamEvent[]): StreamEvent[] {
  if (!cached.length) return server;
  if (!server.length) return cached;
  return cached.length >= server.length ? cached : server;
}

export function thinkingEventsFromSession(s: AgentSession): StreamEvent[] {
  const source = s.thinking_log ?? [];
  return source.map((m) => ({
    kind: m.kind as StreamEvent['kind'],
    content: m.content,
    subagent: (m.subagent as StreamEvent['subagent']) || 'orchestrator',
    meta: m.meta,
    timestamp: m.timestamp,
  }));
}

/** Append a live SSE thinking event, collapsing a repeat of the previous one. */
export function appendThinkingEvent(prev: StreamEvent[], evt: StreamEvent): StreamEvent[] {
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

/** Drop optimistic user bubbles once the server persisted the same text. */
export function pendingOptimisticUserMessages(
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

export function formatFormSubmissionSummary(
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

export function formOptionValue(opt: string | AgentFormFieldOption): string {
  return typeof opt === 'string' ? opt : opt.value;
}

export function formOptionLabel(opt: string | AgentFormFieldOption): string {
  const base = typeof opt === 'string' ? opt : opt.label;
  if (typeof opt !== 'string' && opt.recommended) {
    return `${base} (recommended)`;
  }
  return base;
}

export function fieldInitialValue(field: AgentFormField): unknown {
  if (field.recommended_value !== undefined && field.recommended_value !== null && field.recommended_value !== '') {
    if (field.type === 'checkbox') {
      return Boolean(field.recommended_value);
    }
    return field.recommended_value;
  }
  if (field.type === 'select' && field.options?.length) {
    const recommended = field.options.find(
      (opt) => typeof opt !== 'string' && opt.recommended,
    );
    if (recommended && typeof recommended !== 'string') {
      return recommended.value;
    }
    return formOptionValue(field.options[0]);
  }
  if (field.type === 'checkbox') {
    return field.name === 'confirm_execute';
  }
  return '';
}

export function isStatusMessage(msg: ChatMessage): boolean {
  return msg.kind === 'status';
}
