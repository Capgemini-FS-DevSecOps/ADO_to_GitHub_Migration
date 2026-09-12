/**
 * GAP-012 (GAP-UI-01): LLM provider API key is transmitted in a URL query string.
 *
 * Browser-side half of the gap. `fetchCatalog` (src/lib/llmSettings.ts:102-106)
 * builds a `URLSearchParams`, calls `query.set('api_key', params.apiKey)`, and
 * requests `/v1/settings/llm-models/catalog?<query>`, so the provider key that
 * the operator types on the Settings -> LLM models page (app/settings/models/
 * page.tsx:346-354) ends up in the request URL. URLs are recorded in browser
 * history, devtools/HAR exports, and every forward proxy access log on the path
 * (CWE-598) — Principle V forbids echoing a credential into any of those.
 *
 * The test stubs `fetch` and asserts on the URL actually requested, not on how
 * the request is built, so it stays valid once the key moves into a POST body
 * or a header. The server-side companion is
 * tests/contract/test_gap_012_api_key_in_query_string.py.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';

import { fetchCatalog } from '../lib/llmSettings';

// Obviously-fake literal shaped like the real thing (CA-003 — never a real credential).
const FAKE_PROVIDER_KEY = 'sk-ant-api03-FAKEKEYFORGAP012-not-a-real-credential';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(): { url: string; init?: RequestInit }[] {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify({ source: 'live', entries: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
  return calls;
}

describe('GAP-012 fetchCatalog', () => {
  it('does not place the provider api key in the request URL', async () => {
    const calls = stubFetch();
    await fetchCatalog({ provider: 'anthropic', apiKey: FAKE_PROVIDER_KEY });

    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      expect(call.url).not.toContain(FAKE_PROVIDER_KEY);
      expect(call.url).not.toContain('api_key');
    }
  });

  it('still sends the non-secret provider selector', async () => {
    const calls = stubFetch();
    await fetchCatalog({ provider: 'anthropic', apiKey: FAKE_PROVIDER_KEY });

    const sent = calls.map((c) => `${c.url} ${String(c.init?.body ?? '')}`).join(' ');
    expect(sent).toContain('anthropic');
  });
});
