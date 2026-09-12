/**
 * GAP-025: the LLM models page holds provider API keys.
 *
 * Without `can_manage_models` it must render the denial only — no key input, no catalog
 * control. The request shape for the catalog lookup (GAP-012: the key goes in a POST body,
 * never a query string) is asserted against `fetchCatalog`, which this page calls, in
 * `src/__tests__/gap_012_api_key_in_query_string.test.ts`.
 */
import { describe, it, expect } from 'vitest';

import { modelsAccessDeniedMessage } from '@/lib/permissions';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import LlmModelsPage from './page';

describe('/settings/models', () => {
  it('renders the access denial and no credential input without model permission', () => {
    const html = renderMarkup(<LlmModelsPage />);

    expect(textOf(html)).toBe(modelsAccessDeniedMessage());
    expect(html).not.toContain('type="password"');
    expect(html).not.toContain('Search models');
  });
});
