/**
 * GAP-025: the legacy /dashboard route must keep redirecting to the console home.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { redirect, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import DashboardRedirectPage from './page';

afterEach(() => resetNavState());

describe('/dashboard', () => {
  it('redirects to the console home', () => {
    // A redirect page returns void, never JSX, so it is invoked the way the router invokes
    // it rather than rendered.
    DashboardRedirectPage();
    expect(redirect).toHaveBeenCalledWith('/');
  });
});
