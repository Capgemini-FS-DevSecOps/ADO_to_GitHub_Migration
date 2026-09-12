/**
 * GAP-025: the new-profile page picks the wizard mode from the caller's role, so it must
 * not render the wizard — and its credential inputs — before the session and onboarding
 * status are known.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import NewProfilePage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles/new', () => {
  it('shows the loading state and no wizard before the session is known', () => {
    const html = renderMarkup(<NewProfilePage />);

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('type="password"');
  });
});
