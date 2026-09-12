/**
 * GAP-025: the legacy /migrate route must keep redirecting to the migration settings page,
 * which is where the live-run controls and the gate-override justification field live.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { redirect, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import MigratePage from './page';

afterEach(() => resetNavState());

describe('/migrate', () => {
  it('redirects to /settings/migrate', () => {
    // A redirect page returns void, never JSX, so it is invoked the way the router invokes
    // it rather than rendered.
    MigratePage();
    expect(redirect).toHaveBeenCalledWith('/settings/migrate');
  });
});
