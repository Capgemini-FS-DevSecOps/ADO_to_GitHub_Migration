/**
 * GAP-025: audit history is the platform's read-only trail — logins, profile changes, agent
 * sessions and gate overrides. Its filters and export must be present from first paint, and
 * no audit row may be shown until the server answers.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import HistoryPage from './page';

describe('/settings/history', () => {
  it('offers the filters and CSV export while the history is still loading', () => {
    const text = textOf(renderMarkup(<HistoryPage />));

    expect(text).toContain('Audit & Session History');
    expect(text).toContain('Export CSV');
    expect(text).toContain('All actions');
    expect(text).toContain('Loading history…');
  });
});
