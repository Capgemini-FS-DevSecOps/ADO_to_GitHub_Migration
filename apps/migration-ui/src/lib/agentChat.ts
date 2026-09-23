/**
 * Pure helpers behind AgentChat — server-sent event stream (SSE) thinking-stream reduction, optimistic message
 * dedup, and human-in-the-loop operator prompt (HITL) form defaults/summaries. Kept out of the component so they can be
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

/** Session statuses that mean the agent is still working on the current turn. */
export const ACTIVE_AGENT_STATUSES = new Set([
  'thinking',
  'planning',
  'executing',
  'validating',
]);

const SETTLED_STATUSES = ['completed', 'failed', 'cancelled', 'idle'];

/** Wire values that mean "off", for payloads that still stringify form recommendations. */
const FALSY_WIRE_VALUES = ['false', '0', 'no', 'off', ''];

/**
 * Coerce a human-in-the-loop operator prompt (HITL) form value to a boolean, reading both booleans and the strings
 * older agent payloads sent.
 *
 * The agent service now sends a real JSON boolean for boolean fields (register id GAP-024). This
 * stays as defence in depth: a stringified `False` reads as truthy to `Boolean()`, so
 * every boolean form field goes through here and a recommendation to *not* do
 * something can never read as a yes.
 */
export function parseBooleanValue(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (value === null || value === undefined) return false;
  return !FALSY_WIRE_VALUES.includes(String(value).trim().toLowerCase());
}

/** Report whether a session is mid-turn, either by status or by an unsettled pipeline run. */
export function sessionIsBusy(s: AgentSession | null | undefined): boolean {
  if (!s) return false;
  if (ACTIVE_AGENT_STATUSES.has(s.status ?? '')) return true;
  return Boolean(s.pipeline_run_id && !SETTLED_STATUSES.includes(String(s.status ?? '')));
}

/**
 * Report whether the stop control should be offered: true while the agent is streaming,
 * polling or otherwise busy, and false whenever a human-in-the-loop operator prompt form (HITL) is waiting for a reply.
 */
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

/** Reconcile server and locally cached thinking events by keeping the longer of the two. */
export function mergeThinkingEvents(server: StreamEvent[], cached: StreamEvent[]): StreamEvent[] {
  if (!cached.length) return server;
  if (!server.length) return cached;
  return cached.length >= server.length ? cached : server;
}

/** Rebuild the thinking-stream events from a session's persisted thinking log. */
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

/** Append a live server-sent event stream (SSE) thinking event, collapsing a repeat of the previous one. */
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

/**
 * Render submitted human-in-the-loop operator prompt (HITL) form values as the one-line chat bubble shown after submission.
 * Plan-review forms get a confirmation or change-request sentence; other forms list their
 * set fields, with `dry_run` normalised to a boolean and unset or false fields dropped.
 */
export function formatFormSubmissionSummary(
  values: Record<string, unknown>,
  formId?: string,
): string {
  if (formId === 'intake_plan_review' || formId === 'plan_confirmation') {
    const confirmed = parseBooleanValue(values.plan_confirmed);
    const execute = parseBooleanValue(values.confirm_execute);
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
      } else if (
        raw === 'dry-run' ||
        raw === 'dryrun' ||
        raw === 'dry_run' ||
        raw === 'true' ||
        raw === '1'
      ) {
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

/** Return a form option's submitted value, accepting either the string or object form. */
export function formOptionValue(opt: string | AgentFormFieldOption): string {
  return typeof opt === 'string' ? opt : opt.value;
}

/** Return a form option's display label, suffixed with "(recommended)" when it is flagged. */
export function formOptionLabel(opt: string | AgentFormFieldOption): string {
  const base = typeof opt === 'string' ? opt : opt.label;
  if (typeof opt !== 'string' && opt.recommended) {
    return `${base} (recommended)`;
  }
  return base;
}

/**
 * Pick the initial value for a human-in-the-loop operator prompt (HITL) form field, preferring the agent's recommended value,
 * then the recommended or first select option, and otherwise an empty or unchecked value.
 *
 * Checkboxes default to unchecked, `confirm_execute` included: a preview run (dry-run) is the
 * default, so starting a migration is something the operator ticks, never something a
 * pre-ticked box does for them (safeguard CA-001).
 */
export function fieldInitialValue(field: AgentFormField): unknown {
  if (field.recommended_value !== undefined && field.recommended_value !== null && field.recommended_value !== '') {
    if (field.type === 'checkbox') {
      return parseBooleanValue(field.recommended_value);
    }
    // A boolean field rendered as a select (dry_run) carries a real boolean
    // (register id GAP-024); its option values are strings, so match them.
    return typeof field.recommended_value === 'boolean'
      ? String(field.recommended_value)
      : field.recommended_value;
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
    return false;
  }
  return '';
}

/**
 * Build the starting values for a human-in-the-loop operator prompt (HITL) form's fields.
 *
 * When the session needs platform approval before it may run live, `confirm_execute` is
 * forced off rather than only disabled: a disabled box still submits whatever value it
 * holds, and an execute intent the operator cannot legally give must not reach the agent.
 */
export function initialFormValues(
  fields: AgentFormField[],
  requiresLiveApproval: boolean = false,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    values[field.name] = fieldInitialValue(field);
  }
  if (requiresLiveApproval && 'confirm_execute' in values) {
    values.confirm_execute = false;
  }
  return values;
}

/**
 * Whether a live-execution approval or denial may be submitted.
 *
 * Approving a live run is a one-way escalation, so it takes a second, deliberate
 * click and a written reason that lands in the audit record — never a bare click with an
 * empty justification (safeguard CA-002).
 */
export function liveDecisionReady(reason: string): boolean {
  return reason.trim().length > 0;
}

/** Report whether a chat message is a transient status line rather than chat content. */
export function isStatusMessage(msg: ChatMessage): boolean {
  return msg.kind === 'status';
}

/** Error detail the agent service sends when a submitted form reply targets a form instance it has since replaced (register id GAP-136). */
export const STALE_FORM_ERROR_DETAIL = 'stale_form';

/** Plain-word message shown when a submitted form was replaced by a newer one before the reply reached the server. */
export const STALE_FORM_MESSAGE =
  'This form was replaced by a newer one. The latest form is shown below.';

/**
 * Report whether `error` is the agent service's stale-form rejection (register id GAP-136), so
 * the caller can refetch the session and show the current form instead of a generic failure.
 */
export function isStaleFormError(error: unknown): boolean {
  return error instanceof Error && error.message === STALE_FORM_ERROR_DETAIL;
}
