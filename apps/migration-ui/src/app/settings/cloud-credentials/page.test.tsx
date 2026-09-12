/**
 * GAP-025: cloud credentials are the most sensitive page in the console — it lists detected
 * provider credentials. Without model-management permission it must render the denial and
 * nothing else.
 */
import { describe, it, expect } from 'vitest';

import { modelsAccessDeniedMessage } from '@/lib/permissions';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import CloudCredentialsPage from './page';

describe('/settings/cloud-credentials', () => {
  it('renders the access denial and no credential list without permission', () => {
    const html = renderMarkup(<CloudCredentialsPage />);

    expect(textOf(html)).toBe(modelsAccessDeniedMessage());
    expect(html).not.toContain('Cloud credentials');
    expect(html).not.toContain('<table');
  });
});
