/**
 * This test provides render coverage for the scan-complete banner (register id GAP-025).
 *
 * It is the console's only live-region announcement, so the `role="status"` and the
 * labelled dismiss control are the behaviour, not decoration.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { ScanCompleteBanner } from './ScanCompleteBanner';

describe('ScanCompleteBanner', () => {
  it('announces the message in a live region with a labelled dismiss control', () => {
    const html = renderMarkup(
      <ScanCompleteBanner message="Scan finished: 42 repos" onDismiss={() => {}} />,
    );

    expect(html).toContain('role="status"');
    expect(textOf(html)).toContain('Scan finished: 42 repos');
    expect(html).toContain('aria-label="Dismiss scan notification"');
  });
});
