/**
 * GAP-025: the agent route is a thin frame around `AgentChat`; its job is to mount the chat
 * full-page. Chat behaviour itself is covered by `src/components/AgentChat.test.tsx`.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import AgentPage from './page';

describe('/settings/agent', () => {
  it('mounts the agent chat full-page', () => {
    const html = renderMarkup(<AgentPage />);

    expect(html).toContain('agent-fullpage');
    expect(html).toContain('agent-chat-layout');
    expect(textOf(html)).toContain('New chat');
  });
});
