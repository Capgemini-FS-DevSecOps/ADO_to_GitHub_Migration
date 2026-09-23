/**
 * Cloud credentials are the most sensitive page in the console (register id GAP-025) — it lists detected
 * provider credentials. Without model-management permission it must render the denial and
 * nothing else.
 */
import { describe, it, expect } from 'vitest';

import { credentialDecisionReady } from '@/lib/cloudCredentials';
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

  it('offers no armed credential decision on first paint', () => {
    // Approve, reject and revoke each take a second click, so no "Confirm …"
    // control can exist before one is armed (safeguard CA-002). The per-action rule (only reject records a
    // reason server-side) is covered by `credentialDecisionReady`.
    const text = textOf(renderMarkup(<CloudCredentialsPage />));

    expect(text).not.toContain('Confirm approve');
    expect(text).not.toContain('Confirm revoke');
    expect(credentialDecisionReady('reject', '')).toBe(false);
  });
});
