/**
 * GAP-025: the token edit page must render the spinner rather than an empty edit form while
 * the profile loads — a blank form submitted against a real token id would overwrite a
 * working PAT.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import EditTokenPage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles/[profileId]/tokens/[tokenId]', () => {
  it('shows the loading state and no edit form before the profile arrives', () => {
    const html = renderMarkup(
      <EditTokenPage params={{ profileId: 'prof-1', tokenId: 'tok-1' }} />,
    );

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('Edit GitHub token');
    expect(html).not.toContain('type="password"');
  });
});
