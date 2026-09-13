import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  credentialDecisionReady,
  fetchCloudCredentials,
  rejectCloudCredential,
} from './cloudCredentials';

describe('cloudCredentials API helpers', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetchCloudCredentials requests the scan query parameter', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ sources: [] }), { status: 200 }),
    );
    await fetchCloudCredentials(true);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/v1/settings/cloud-credentials?scan=true'),
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('rejectCloudCredential sends the operator reason the server records', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ provider: 'aws' }), { status: 200 }),
    );
    await rejectCloudCredential('aws', 'no change window');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/v1/settings/cloud-credentials/aws/reject'),
      expect.objectContaining({ body: JSON.stringify({ reason: 'no change window' }) }),
    );
  });
});

describe('credential decision confirmation', () => {
  it('blocks a rejection with no written reason', () => {
    expect(credentialDecisionReady('reject', '')).toBe(false);
    expect(credentialDecisionReady('reject', '  ')).toBe(false);
    expect(credentialDecisionReady('reject', 'unapproved account')).toBe(true);
  });

  it('needs only the armed confirm step where the API records no reason', () => {
    expect(credentialDecisionReady('approve', '')).toBe(true);
    expect(credentialDecisionReady('revoke', '')).toBe(true);
  });
});
