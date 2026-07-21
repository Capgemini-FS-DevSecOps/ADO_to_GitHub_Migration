import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fetchCloudCredentials } from './cloudCredentials';

describe('cloudCredentials API helpers', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetchCloudCredentials requests scan by default', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ sources: [] }), { status: 200 }),
    );
    await fetchCloudCredentials(true);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/v1/settings/cloud-credentials?scan=true'),
      expect.objectContaining({ credentials: 'include' }),
    );
  });
});
