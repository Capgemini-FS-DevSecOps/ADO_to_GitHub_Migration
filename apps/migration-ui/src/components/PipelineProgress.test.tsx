/**
 * This test provides render coverage for the run pipeline bar (register id GAP-025).
 *
 * The full and compact bars are two exports over one private `PipelineBar`, so the only
 * thing distinguishing them is what the compact variant drops — the labels row and the
 * larger icons. That split is invisible to the type checker and was untested.
 */
import { describe, it, expect } from 'vitest';

import type { PipelineStep } from '@/lib/types';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { CompactStepPipelineBar, StepPipelineBar, StepStatusBadge } from './PipelineProgress';

const STEPS: PipelineStep[] = [
  { id: 'connect', label: 'Connect', description: 'Credentials', status: 'completed' },
  { id: 'migrate', label: 'Migrate', description: 'Push repos', status: 'running' },
  { id: 'validate', label: 'Validate', description: 'Compare SHAs', status: 'pending' },
];

describe('StepPipelineBar', () => {
  it('shows one icon and one label per step', () => {
    const html = renderMarkup(<StepPipelineBar steps={STEPS} />);

    expect(textOf(html)).toBe('Connect Migrate Validate');
    expect(html.match(/pipeline-step-node/g)).toHaveLength(3);
    expect(html).toContain('title="Connect: completed"');
    expect(html).toContain('width:28px');
  });

  it('renders nothing when a run has no steps', () => {
    expect(renderMarkup(<StepPipelineBar steps={[]} />)).toBe('');
  });

  it('draws a connector between steps but not after the last one', () => {
    const html = renderMarkup(<StepPipelineBar steps={STEPS} />);
    expect(html.match(/pipeline-step-connector/g)).toHaveLength(STEPS.length - 1);
  });
});

describe('CompactStepPipelineBar', () => {
  it('drops the labels row and shrinks the icons', () => {
    const html = renderMarkup(<CompactStepPipelineBar steps={STEPS} />);

    expect(textOf(html)).toBe('');
    expect(html).toContain('pipeline-bar-compact');
    expect(html).not.toContain('pipeline-labels-row');
    expect(html).toContain('width:20px');
    expect(html.match(/pipeline-step-node/g)).toHaveLength(3);
  });
});

describe('StepStatusBadge', () => {
  it.each([
    ['completed', 'completed', 'step-badge-completed'],
    ['running', 'running', 'step-badge-running'],
    ['failed', 'failed', 'step-badge-failed'],
    ['dry_run_complete', 'dry run', 'step-badge-dry_run'],
    ['awaiting_approval', 'needs approval', 'step-badge-awaiting_approval'],
  ])('renders %s as "%s"', (status, label, css) => {
    const html = renderMarkup(<StepStatusBadge status={status} />);
    expect(textOf(html)).toBe(label);
    expect(html).toContain(css);
  });
});
