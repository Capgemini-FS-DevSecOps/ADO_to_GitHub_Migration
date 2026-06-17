import { describe, expect, it } from 'vitest';
import { canEnableModel, validationBadgeLabel } from './llmSettings';

describe('llmSettings helpers', () => {
  it('allows enable only after validation passed', () => {
    expect(canEnableModel('passed')).toBe(true);
    expect(canEnableModel('never_validated')).toBe(false);
    expect(canEnableModel('failed')).toBe(false);
  });

  it('maps validation badge labels', () => {
    expect(validationBadgeLabel('passed')).toBe('Passed');
    expect(validationBadgeLabel('failed')).toBe('Failed');
    expect(validationBadgeLabel(undefined)).toBe('Not validated');
  });
});
