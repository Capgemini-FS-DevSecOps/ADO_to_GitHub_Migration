/**
 * GAP-025: the legacy /agent route must keep redirecting to the agent chat under settings.
 * Bookmarks and the docs both point at the old path.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { redirect, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import AgentPage from './page';

afterEach(() => resetNavState());

describe('/agent', () => {
  it('redirects to /settings/agent', () => {
    // A redirect page returns void, never JSX, so it is invoked the way the router invokes
    // it rather than rendered.
    AgentPage();
    expect(redirect).toHaveBeenCalledWith('/settings/agent');
    expect(redirect).toHaveBeenCalledTimes(1);
  });
});
