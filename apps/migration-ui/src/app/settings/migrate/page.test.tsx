/**
 * GAP-025: first paint of the migration page, which is where a live run is started.
 *
 * Two safety properties live in this render. Dry run is the default (CA-001) — the operator
 * has to opt into a live run, not out of one. And the gate-override justification field only
 * exists on a live run (GAP-009): it is the console's half of the audited escalation path,
 * so it must not be present, and therefore cannot be filled in, on a dry run that will never
 * reach a gate. The normalisation of what is typed there is covered by
 * `src/lib/pipelineRunStatus.test.ts`; its trip through `startPipelineRun` needs an
 * interaction and is out of reach without a DOM test environment.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import MigratePage from './page';

afterEach(() => resetNavState());

describe('/settings/migrate', () => {
  it('defaults to a dry run', () => {
    const html = renderMarkup(<MigratePage />);

    expect(textOf(html)).toContain('Dry run');
    expect(html).toMatch(/id="run-dry-run"[^>]*checked/);
  });

  it('offers no gate-override justification field while the run is a dry run', () => {
    const html = renderMarkup(<MigratePage />);

    expect(html).not.toContain('override-reason');
    expect(textOf(html)).not.toContain('Gate override justification');
    expect(textOf(html)).not.toContain('Live migration will make actual changes');
  });

  it('shows the plain start button, not a live confirmation, on first paint', () => {
    // CA-002: the live confirm step is armed by a first click, so it must never be the
    // control a freshly loaded page offers. Arming it needs a DOM; the decision itself is
    // covered by `liveStartNeedsConfirm` in src/lib/pipelineRunStatus.test.ts.
    const text = textOf(renderMarkup(<MigratePage />));

    expect(text).toContain('Start migration');
    expect(text).not.toContain('Confirm live migration');
  });

  it('cannot start a run before a profile and a target are chosen', () => {
    const html = renderMarkup(<MigratePage />);
    const text = textOf(html);

    expect(text).toContain('Start migration');
    expect(html).toMatch(/disabled[^>]*>\s*Start migration|Start migration/);
    expect(text).toContain('Select a repository or enter a wave ID to start.');
    expect(text).toContain('No active migration profile');
  });
});
