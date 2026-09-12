/**
 * GAP-025: the profile list is the console's landing page. Until settings load it shows the
 * spinner and no profile rows — in particular no "create profile" action, which is gated on
 * a permission that is only known once the session resolves.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import ProfilesPage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles', () => {
  it('shows the loading state and no profile actions before settings arrive', () => {
    const html = renderMarkup(<ProfilesPage />);

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('<table');
    expect(html).not.toContain('New migration profile');
  });
});
