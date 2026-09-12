/**
 * GAP-025: the token list shows a profile's GitHub PATs by name and validation state. Until
 * the profile loads it must render the spinner and no token rows.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import ProfileTokensPage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles/[profileId]/tokens', () => {
  it('shows the loading state and no token rows before the profile arrives', () => {
    const html = renderMarkup(<ProfileTokensPage params={{ profileId: 'prof-1' }} />);

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('<table');
    expect(html).not.toContain('Add token');
  });
});
