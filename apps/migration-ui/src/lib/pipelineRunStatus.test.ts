import { describe, expect, it } from 'vitest';
import {
  gateOverrideReason,
  mapPipelineRunStatus,
  pipelineRunNeedsApproval,
  pipelineRunStatusLabel,
} from './pipelineRunStatus';

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

describe('gateOverrideReason', () => {
  it('sends the trimmed justification for a live run', () => {
    expect(gateOverrideReason(false, '  exec sign-off, POC failure accepted  ')).toBe(
      'exec sign-off, POC failure accepted',
    );
  });

  it('sends nothing for a whitespace-only justification', () => {
    expect(gateOverrideReason(false, '   ')).toBe('');
  });

  it('sends nothing on a dry run, which never reaches the gate', () => {
    expect(gateOverrideReason(true, 'typed then switched back to dry run')).toBe('');
  });
});
