import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  canEnableModel,
  proxyPasswordUpdate,
  updateConnectivity,
  validationBadgeLabel,
} from './llmSettings';

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

/**
 * A blank password box used to send the `***` keep-mask unconditionally, so a
 * stored proxy password could never be removed from the console (register id GAP-061). The console has no DOM
 * test environment (see `__tests__/renderMarkup`), so the clear control is pinned at the
 * two points a click passes through: the value it builds, and the body it puts.
 */
describe('proxy password clearing', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps the stored password when the box is left blank', () => {
    expect(proxyPasswordUpdate('')).toBe('***');
  });

  it('sends a typed password as the replacement', () => {
    expect(proxyPasswordUpdate('not-a-real-proxy-pass')).toBe('not-a-real-proxy-pass');
  });

  it('sends the clear sentinel for the clear control, whatever is in the box', () => {
    expect(proxyPasswordUpdate('', { clear: true })).toBe('');
    expect(proxyPasswordUpdate('half-typed', { clear: true })).toBe('');
  });

  it('puts the clear sentinel and nothing else, so no half-typed field is saved with it', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ proxy_password: '' }), { status: 200 }),
    );

    await updateConnectivity({ proxy_password: proxyPasswordUpdate('', { clear: true }) });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/settings\/connectivity$/);
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ proxy_password: '' });
  });
});
