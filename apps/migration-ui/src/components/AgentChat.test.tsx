/**
 * GAP-025: render coverage for the agent chat surface.
 *
 * `AgentChat` is the console's largest component and carries the live-execution approval
 * controls (CA-001). What a server render can pin is first paint with no session loaded:
 * the composer is gated on a configured model, and none of the live-run controls —
 * "Approve live run", "Deny", "Request live execution" — exist before a session asks for
 * them. Their *enabled* states depend on `live_approval_status` arriving from the agent
 * service through an effect, so they stay out of reach until a DOM test environment is
 * approved (see the note in `@/__tests__/renderMarkup`).
 *
 * The GAP-024 form-field defect (stringified `recommended_value`) is covered on the browser
 * side by `src/lib/agentChat.test.ts` — `parseBooleanValue`, `fieldInitialValue` and
 * `initialFormValues`, which this component's private `AgentFormPanel` calls. The panel
 * itself is not exported and only renders once a session has a `pending_form`, so it is not
 * reachable from a render test; the same goes for arming the live-decision confirm step,
 * whose submit rule is pinned by `liveDecisionReady`.
 */
import { describe, it, expect } from 'vitest';

import { liveDecisionReady } from '@/lib/agentChat';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { AgentChat } from './AgentChat';

describe('AgentChat', () => {
  it('renders the session sidebar with a way to start a chat', () => {
    const text = textOf(renderMarkup(<AgentChat />));

    expect(text).toContain('Chats');
    expect(text).toContain('New chat');
  });

  it('tells the operator to configure a model and blocks the composer until one exists', () => {
    const html = renderMarkup(<AgentChat />);

    expect(textOf(html)).toContain('No LLM models are configured');
    expect(html).toContain('disabled');
    expect(html).toContain('agent-chat-messages');
  });

  it('shows no live-execution controls before a session requests one', () => {
    const text = textOf(renderMarkup(<AgentChat />));

    expect(text).not.toContain('Approve live run');
    expect(text).not.toContain('Deny');
    expect(text).not.toContain('Request live execution');
    expect(text).not.toContain('Live execution pending platform approval');
  });

  it('never offers an armed live decision, and needs a reason to submit one', () => {
    // CA-002: "Approve live run" arms a confirm step rather than approving, so the
    // committing control cannot be on screen before the operator asks for it.
    const text = textOf(renderMarkup(<AgentChat />));

    expect(text).not.toContain('Confirm approve');
    expect(text).not.toContain('Confirm deny');
    expect(liveDecisionReady('')).toBe(false);
    expect(liveDecisionReady('approved in CAB-4821')).toBe(true);
  });
});
