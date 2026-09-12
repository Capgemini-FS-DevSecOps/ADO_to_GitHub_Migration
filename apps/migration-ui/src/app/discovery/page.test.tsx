/**
 * GAP-025: the legacy /discovery route must keep redirecting to the discovery settings tab.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { redirect, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import DiscoveryPage from './page';

afterEach(() => resetNavState());

describe('/discovery', () => {
  it('redirects to /settings/discovery', () => {
    // A redirect page returns void, never JSX, so it is invoked the way the router invokes
    // it rather than rendered.
    DiscoveryPage();
    expect(redirect).toHaveBeenCalledWith('/settings/discovery');
  });
});
