import { describe, expect, it } from 'vitest';
import { mergeSessionLists, truncateTitle } from './agentSessions';

describe('agentSessions', () => {
  it('truncates long titles', () => {
    const long = 'a'.repeat(80);
    expect(truncateTitle(long)).toHaveLength(56);
    expect(truncateTitle(long).endsWith('…')).toBe(true);
  });

  it('merges remote status into local list without reordering', () => {
    const merged = mergeSessionLists(
      [
        { sessionId: 'a', title: 'Local', updatedAt: '2026-01-01T00:00:00Z', status: 'idle' },
        { sessionId: 'b', title: 'Second', updatedAt: '2026-01-02T00:00:00Z' },
      ],
      [
        { sessionId: 'a', title: 'Remote newer', updatedAt: '2026-06-01T00:00:00Z', status: 'thinking' },
        { sessionId: 'c', title: 'Remote only', updatedAt: '2026-06-02T00:00:00Z' },
      ],
    );
    expect(merged.map((entry) => entry.sessionId)).toEqual(['a', 'b', 'c']);
    expect(merged[0].title).toBe('Local');
    expect(merged[0].status).toBe('thinking');
    expect(merged[2].title).toBe('Remote only');
  });
});
