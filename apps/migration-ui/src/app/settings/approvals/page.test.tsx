/**
 * The live-execution approval queue is the console's half of the safeguard that keeps a
 * preview run (dry-run) as the default and requires an explicit opt-in for a real (live)
 * run (safeguard CA-001; register id GAP-025).
 *
 * Its first duty is to show nothing to anyone without `can_approve_live_execution` — no
 * queue, no approve/deny controls — which is exactly the state a render with no session
 * produces.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import LiveApprovalsPage from './page';

describe('/settings/approvals', () => {
  it('shows neither the queue nor any decision control without approver permission', () => {
    const html = renderMarkup(<LiveApprovalsPage />);
    const text = textOf(html);

    expect(text).toBe('Live execution approvals are visible to platform admins and approvers only.');
    expect(text).not.toContain('Approve');
    expect(text).not.toContain('Deny');
    expect(html).not.toContain('<button');
    expect(html).not.toContain('<textarea');
  });
});
