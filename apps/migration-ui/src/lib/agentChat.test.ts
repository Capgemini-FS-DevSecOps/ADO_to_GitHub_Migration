import { describe, expect, it } from 'vitest';
import {
  appendThinkingEvent,
  fieldInitialValue,
  formatFormSubmissionSummary,
  formOptionLabel,
  isAgentInterruptible,
  mergeThinkingEvents,
  pendingOptimisticUserMessages,
  sessionIsBusy,
  thinkingEventsFromSession,
  type ChatMessage,
} from './agentChat';
import type { AgentFormField, AgentSession, StreamEvent } from './agent';

const session = (patch: Partial<AgentSession>): AgentSession =>
  ({ session_id: 's1', status: 'idle', ...patch }) as AgentSession;

const thinking = (content: string, subagent = 'planner'): StreamEvent =>
  ({ kind: 'thinking', content, subagent }) as StreamEvent;

const userMsg = (id: string, content: string): ChatMessage => ({
  id,
  role: 'user',
  content,
  kind: 'message',
});

describe('thinking stream reduction', () => {
  it('collapses a repeat of the previous event but keeps later repeats apart', () => {
    const first = appendThinkingEvent([], thinking('Scanning repos'));
    const duplicate = appendThinkingEvent(first, thinking('Scanning repos'));
    expect(duplicate).toBe(first);

    const next = appendThinkingEvent(duplicate, thinking('Ordering waves'));
    const again = appendThinkingEvent(next, thinking('Scanning repos'));
    expect(again.map((e) => e.content)).toEqual([
      'Scanning repos',
      'Ordering waves',
      'Scanning repos',
    ]);
  });

  it('treats the same text from a different subagent as a new event', () => {
    const prev = appendThinkingEvent([], thinking('Checking token', 'planner'));
    const next = appendThinkingEvent(prev, thinking('Checking token', 'executor'));
    expect(next).toHaveLength(2);
  });

  it('maps the server thinking log and defaults the subagent', () => {
    const events = thinkingEventsFromSession(
      session({
        thinking_log: [
          { role: 'assistant', content: 'Planning', kind: 'thinking', subagent: 'planner' },
          { role: 'assistant', content: 'Calling tool', kind: 'tool_call' },
        ],
      }),
    );
    expect(events).toHaveLength(2);
    expect(events[0].subagent).toBe('planner');
    expect(events[1].subagent).toBe('orchestrator');
  });

  it('keeps whichever of server or cache has more events', () => {
    const server = [thinking('a')];
    const cached = [thinking('a'), thinking('b')];
    expect(mergeThinkingEvents(server, cached)).toBe(cached);
    expect(mergeThinkingEvents(cached, [])).toBe(cached);
    expect(mergeThinkingEvents([], cached)).toBe(cached);
  });
});

describe('optimistic message dedup', () => {
  it('drops bubbles the server echoed back and keeps unmatched ones', () => {
    const pending = pendingOptimisticUserMessages(
      [userMsg('u1', 'Migrate Core/Api'), userMsg('u2', 'And then validate')],
      [userMsg('s1', 'Migrate Core/Api'), { id: 's2', role: 'assistant', content: 'Sure', kind: 'message' }],
    );
    expect(pending.map((m) => m.id)).toEqual(['u2']);
  });

  it('pairs duplicates one-for-one rather than dropping all of them', () => {
    const pending = pendingOptimisticUserMessages(
      [userMsg('u1', 'retry'), userMsg('u2', 'retry')],
      [userMsg('s1', 'retry')],
    );
    expect(pending.map((m) => m.id)).toEqual(['u2']);
  });

  it('matches Python-cased booleans echoed by the agent', () => {
    const pending = pendingOptimisticUserMessages(
      [userMsg('u1', 'dry_run: True')],
      [userMsg('s1', 'dry_run: true')],
    );
    expect(pending).toEqual([]);
  });
});

describe('interrupt / busy state', () => {
  it('is never interruptible while a form is awaiting the operator', () => {
    expect(isAgentInterruptible(session({ pending_form: { form_id: 'f', title: 'T', fields: [] } }), { streaming: true })).toBe(false);
    expect(isAgentInterruptible(null, { streaming: true })).toBe(false);
  });

  it('is interruptible while streaming, working, or running a pipeline', () => {
    expect(isAgentInterruptible(session({}), { streaming: true })).toBe(true);
    expect(isAgentInterruptible(session({ status: 'executing' }))).toBe(true);
    expect(isAgentInterruptible(session({ pipeline_run_id: 'r1', status: 'running' }))).toBe(true);
    expect(isAgentInterruptible(session({}))).toBe(false);
    expect(isAgentInterruptible(session({ pipeline_run_id: 'r1', status: 'completed' }))).toBe(false);
  });

  it('reports a session busy on active status or an unfinished pipeline run', () => {
    expect(sessionIsBusy(null)).toBe(false);
    expect(sessionIsBusy(session({ status: 'validating' }))).toBe(true);
    expect(sessionIsBusy(session({ status: 'queued', pipeline_run_id: 'r1' }))).toBe(true);
    expect(sessionIsBusy(session({ status: 'failed', pipeline_run_id: 'r1' }))).toBe(false);
  });
});

describe('HITL form defaults', () => {
  const field = (patch: Partial<AgentFormField>): AgentFormField =>
    ({ name: 'f', label: 'F', type: 'text', ...patch }) as AgentFormField;

  it('prefers recommended_value, coercing it for checkboxes', () => {
    expect(fieldInitialValue(field({ type: 'text', recommended_value: 'main' }))).toBe('main');
    expect(fieldInitialValue(field({ type: 'checkbox', recommended_value: 'yes' }))).toBe(true);
  });

  it('picks the recommended option for a select, else the first', () => {
    const options = [
      { value: 'mirror', label: 'Mirror' },
      { value: 'gei', label: 'GEI', recommended: true },
    ];
    expect(fieldInitialValue(field({ type: 'select', options }))).toBe('gei');
    expect(fieldInitialValue(field({ type: 'select', options: ['mirror', 'gei'] }))).toBe('mirror');
    expect(fieldInitialValue(field({ type: 'select' }))).toBe('');
  });

  it('pre-ticks only the confirm_execute checkbox', () => {
    expect(fieldInitialValue(field({ name: 'confirm_execute', type: 'checkbox' }))).toBe(true);
    expect(fieldInitialValue(field({ name: 'archive_ado', type: 'checkbox' }))).toBe(false);
  });

  it('labels recommended options', () => {
    expect(formOptionLabel({ value: 'gei', label: 'GEI', recommended: true })).toBe('GEI (recommended)');
    expect(formOptionLabel('mirror')).toBe('mirror');
  });
});

describe('form submission summary', () => {
  it('summarises plan confirmation, with and without execution', () => {
    expect(formatFormSubmissionSummary({ plan_confirmed: true }, 'plan_confirmation')).toBe('Plan confirmed');
    expect(
      formatFormSubmissionSummary({ plan_confirmed: true, confirm_execute: true }, 'intake_plan_review'),
    ).toBe('Plan confirmed, start migration');
  });

  it('reports requested changes when the plan is not confirmed', () => {
    expect(formatFormSubmissionSummary({ plan_notes: '  drop wave 3  ' }, 'plan_confirmation')).toBe(
      'Requested plan changes: drop wave 3',
    );
    expect(formatFormSubmissionSummary({}, 'plan_confirmation')).toBe('Plan review submitted');
  });

  it('normalises dry_run from the select strings the form uses', () => {
    expect(formatFormSubmissionSummary({ dry_run: 'live' })).toBe('dry_run: false');
    expect(formatFormSubmissionSummary({ dry_run: 'dry-run' })).toBe('dry_run: true');
    expect(formatFormSubmissionSummary({ dry_run: false })).toBe('dry_run: false');
    expect(formatFormSubmissionSummary({ dry_run: '' })).toBe('');
  });

  it('omits empty values and unticked checkboxes', () => {
    expect(
      formatFormSubmissionSummary({ scope: 'repos', notes: '', archive_ado: false, include_prs: true }),
    ).toBe('scope: repos, include_prs: true');
  });
});
