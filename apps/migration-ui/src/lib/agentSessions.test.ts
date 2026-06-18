import { describe, expect, it } from 'vitest';
import { mergeSessionLists, truncateTitle } from './agentSessions';

describe('agentSessions', () => {
  it('truncates long titles', () => {
    const long = 'a'.repeat(80);
    expect(truncateTitle(long)).toHaveLength(56);
    expect(truncateTitle(long).endsWith('…')).toBe(true);
  });

  it('merges local and remote session lists by newest updated_at', () => {
    const merged = mergeSessionLists(
      [{ sessionId: 'a', title: 'Local', updatedAt: '2026-01-01T00:00:00Z' }],
      [{ sessionId: 'a', title: 'Remote newer', updatedAt: '2026-06-01T00:00:00Z' }],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].title).toBe('Remote newer');
  });
});
