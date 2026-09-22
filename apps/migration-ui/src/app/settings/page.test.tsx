/**
 * The settings index must land on the profiles page — no profile, no console (register id GAP-025).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { redirect, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import SettingsIndexPage from './page';

afterEach(() => resetNavState());

describe('/settings', () => {
  it('redirects to /settings/profiles', () => {
    // A redirect page returns void, never JSX, so it is invoked the way the router invokes
    // it rather than rendered.
    SettingsIndexPage();
    expect(redirect).toHaveBeenCalledWith('/settings/profiles');
  });
});
