/**
 * GAP-025: advanced settings render a spinner until the accelerator settings arrive, and
 * nothing at all if the response carries no `advanced` block — so a backend that drops the
 * block leaves a blank page rather than a half-populated form of stale defaults.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';

import AdvancedSettingsPage from './page';

describe('/settings/advanced', () => {
  it('shows the loading state and no defaults form before settings arrive', () => {
    const html = renderMarkup(<AdvancedSettingsPage />);

    expect(html).toContain('oai-spinner');
    expect(html).not.toContain('Accelerator defaults');
    expect(html).not.toContain('<input');
  });
});
