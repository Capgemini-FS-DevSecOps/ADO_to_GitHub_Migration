/**
 * The pending-approval queue must not render an approval verdict before the queue
 * has loaded (register id GAP-025).
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';

import PendingProfilesPage from './page';

describe('/settings/profiles/pending', () => {
  it('shows the loading state and no approval controls before the queue arrives', () => {
    const html = renderMarkup(<PendingProfilesPage />);

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('Pending profile approval');
    expect(html).not.toContain('<button');
  });
});
