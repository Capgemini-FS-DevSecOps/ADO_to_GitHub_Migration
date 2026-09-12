/**
 * GAP-025: a profile's index route must land on that profile's source page, carrying the
 * profile id through — a redirect built from route params, so a wrong template string sends
 * the operator to another profile's connection settings.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { redirect, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import ProfileIndexPage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles/[profileId]', () => {
  it('redirects to the source page of the requested profile', () => {
    // A redirect page returns void, never JSX, so it is invoked the way the router invokes
    // it rather than rendered.
    ProfileIndexPage({ params: { profileId: 'prof-42' } });
    expect(redirect).toHaveBeenCalledWith('/settings/profiles/prof-42/source');
  });
});
