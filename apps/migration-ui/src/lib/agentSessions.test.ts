import { afterEach, describe, expect, it } from 'vitest';
import { clearAgentStorage, mergeSessionLists, truncateTitle } from './agentSessions';

/**
 * Minimal in-memory `localStorage`: the suite runs on node with no DOM environment, and
 * a project requirement forbids adding one (FR-008), so the few storage helpers are exercised against a stub that
 * implements only what they call.
 */
function stubLocalStorage(seed: Record<string, string>) {
  const store = new Map(Object.entries(seed));
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      get length() {
        return store.size;
      },
      key: (i: number) => [...store.keys()][i] ?? null,
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
      clear: () => store.clear(),
    },
  });
  return store;
}

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

describe('clearAgentStorage', () => {
  afterEach(() => {
    Reflect.deleteProperty(globalThis, 'localStorage');
  });

  it('purges every cached transcript and thinking log, leaving other keys alone', () => {
    const store = stubLocalStorage({
      'ado2gh-agent-chat:alice:p1:s1': '[{"role":"user","content":"migrate repo-a"}]',
      'ado2gh-agent-thinking:alice:p1:s1': '[{"kind":"tool_call","content":"list_repos"}]',
      'ado2gh-agent-turn-thinking:alice:p1:s1:0': '[]',
      'ado2gh-agent-sessions:alice:p1': '[]',
      'ado2gh-agent-active:alice:p1': 's1',
      'theme': 'dark',
    });

    clearAgentStorage();

    expect([...store.keys()]).toEqual(['theme']);
  });

  it('is a no-op when there is no browser storage', () => {
    expect(() => clearAgentStorage()).not.toThrow();
  });
});
