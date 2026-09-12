/**
 * GAP-025: connectivity settings carry the proxy password and the CA bundle, so they sit
 * behind the same model-management permission as the LLM pages.
 */
import { describe, it, expect } from 'vitest';

import { modelsAccessDeniedMessage } from '@/lib/permissions';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import ConnectivitySettingsPage from './page';

describe('/settings/connectivity', () => {
  it('renders the access denial and no proxy credential input without permission', () => {
    const html = renderMarkup(<ConnectivitySettingsPage />);

    expect(textOf(html)).toBe(modelsAccessDeniedMessage());
    expect(html).not.toContain('type="password"');
    expect(html).not.toContain('Model override');
  });
});
