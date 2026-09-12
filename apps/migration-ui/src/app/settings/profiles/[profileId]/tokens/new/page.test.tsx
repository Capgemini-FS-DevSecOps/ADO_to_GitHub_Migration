/**
 * GAP-025: the add-token form takes a GitHub PAT. The token input must be masked (CA-003),
 * and the form must render against the profile named in the route rather than a blank one.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import NewTokenPage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles/[profileId]/tokens/new', () => {
  it('renders the add-token form with a masked token field', () => {
    const html = renderMarkup(<NewTokenPage params={{ profileId: 'prof-1' }} />);
    const text = textOf(html);

    expect(text).toContain('Add GitHub token');
    expect(text).toContain('Save token');
    expect(text).toContain('Test connection');
    expect(html).toContain('type="password"');
    expect(html).not.toMatch(/name="token"[^>]*type="text"/);
  });
});
