import { describe, expect, it } from 'vitest';
import { mapPipelineRunStatus, pipelineRunNeedsApproval, pipelineRunStatusLabel } from './pipelineRunStatus';

describe('pipelineRunStatus', () => {
  it('maps awaiting_approval distinctly from pending', () => {
    expect(mapPipelineRunStatus('awaiting_approval')).toBe('awaiting_approval');
    expect(mapPipelineRunStatus('pending')).toBe('pending');
  });

  it('labels awaiting_approval as needs approval', () => {
    expect(pipelineRunStatusLabel('awaiting_approval')).toBe('needs approval');
    expect(pipelineRunNeedsApproval('awaiting_approval')).toBe(true);
    expect(pipelineRunNeedsApproval('pending')).toBe(false);
  });
});
