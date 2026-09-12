/**
 * GAP-025: render coverage for per-repo run results.
 *
 * The panel hides itself entirely unless a step carries repo detail, which is why an empty
 * "Repo results" card never appears mid-run — and why a regression in that guard would show
 * an empty card on every run instead.
 */
import { describe, it, expect } from 'vitest';

import type { PipelineStep } from '@/lib/types';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { RunStepDetails } from './RunStepDetails';

const step = (over: Partial<PipelineStep>): PipelineStep => ({
  id: 'migrate',
  label: 'Migrate',
  description: 'Push repos',
  status: 'completed',
  message: '2 repos migrated',
  ...over,
});

describe('RunStepDetails', () => {
  it('renders nothing for a run with no per-repo detail', () => {
    expect(renderMarkup(<RunStepDetails steps={[]} />)).toBe('');
    expect(renderMarkup(<RunStepDetails steps={[step({})]} />)).toBe('');
    expect(renderMarkup(<RunStepDetails steps={[step({ result: {} })]} />)).toBe('');
  });

  it('shows the migrate step label, message and repo rows once details arrive', () => {
    const html = renderMarkup(
      <RunStepDetails
        steps={[
          step({
            result: {
              repo_details: [
                { repo: 'Payments/svc-a', status: 'completed', summary: 'mirrored' },
                { repo: 'Payments/svc-b', status: 'failed', errors: ['push rejected'] },
              ],
            },
          }),
        ]}
      />,
    );
    const text = textOf(html);

    expect(text).toContain('Repo results');
    expect(text).toContain('Migrate');
    expect(text).toContain('2 repos migrated');
    expect(text).toContain('Payments/svc-a');
    expect(text).toContain('Payments/svc-b');
  });

  it('adds a validation section when the validate step reports repos', () => {
    const html = renderMarkup(
      <RunStepDetails
        steps={[
          step({
            id: 'validate',
            label: 'Validate',
            message: 'commit SHAs compared',
            result: {
              repo_details: [
                {
                  project: 'Payments',
                  repo: 'svc-a',
                  gh_target: 'contoso/svc-a',
                  overall: 'PASS',
                },
              ],
            },
          }),
        ]}
      />,
    );
    const text = textOf(html);

    expect(text).toContain('Validation');
    expect(text).toContain('commit SHAs compared');
    expect(text).toContain('contoso/svc-a');
  });

  it('ignores repo detail hung off a step that is not a migrate or validate step', () => {
    expect(
      renderMarkup(
        <RunStepDetails
          steps={[step({ id: 'connect', label: 'Connect', result: { repo_details: [] } })]}
        />,
      ),
    ).toBe('');
  });
});
