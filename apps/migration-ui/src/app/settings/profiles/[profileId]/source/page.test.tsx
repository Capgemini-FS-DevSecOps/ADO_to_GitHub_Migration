/**
 * The source-connection page edits an Azure DevOps personal access token (register id GAP-025). It must render the spinner — not an
 * empty form bound to no profile — until the profile has loaded, or a save would post
 * blank credentials over a live connection.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import ProfileSourcePage from './page';

afterEach(() => resetNavState());

describe('/settings/profiles/[profileId]/source', () => {
  it('shows the loading state and no connection form before the profile arrives', () => {
    const html = renderMarkup(<ProfileSourcePage params={{ profileId: 'prof-1' }} />);

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('Source connection (Azure DevOps)');
    expect(html).not.toContain('type="password"');
  });
});
