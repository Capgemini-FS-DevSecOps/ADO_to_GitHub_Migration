'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useState } from 'react';
import {
  approveCloudCredential,
  fetchCloudCredentials,
  patchCloudCredential,
  rejectCloudCredential,
  rescanCloudCredentials,
  revokeCloudCredential,
  type CloudCredentialSource,
} from '@/lib/cloudCredentials';
import { fetchSession } from '@/lib/auth';
import { canManageModels, modelsAccessDeniedMessage } from '@/lib/permissions';

function statusBadge(source: CloudCredentialSource): string {
  if (source.completeness === 'absent') {
    return 'Not detected';
  }
  if (source.completeness === 'incomplete') {
    return 'Incomplete configuration';
  }
  switch (source.status) {
    case 'approved':
      return 'Approved';
    case 'rejected':
      return 'Rejected';
    case 'revoked':
      return 'Revoked';
    default:
      return 'Pending approval';
  }
}

function noProvidersDetected(sources: CloudCredentialSource[]): boolean {
  return sources.length === 0 || sources.every((s) => s.completeness === 'absent');
}

function SourceCard({
  source,
  onRefresh,
}: {
  source: CloudCredentialSource;
  onRefresh: () => void;
}) {
  const [region, setRegion] = useState(source.region ?? '');
  const [endpoint, setEndpoint] = useState(source.endpoint ?? '');
  const [project, setProject] = useState(source.project ?? '');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="oai-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <h3 className="oai-subsection-title" style={{ margin: 0, textTransform: 'uppercase' }}>
          {source.provider}
        </h3>
        <span className="form-hint">{statusBadge(source)}</span>
      </div>
      <p className="form-hint" style={{ marginTop: 8 }}>
        {source.service} · {source.completeness}
        {source.primary_method ? ` · ${source.primary_method}` : ''}
      </p>
      {source.missing_fields.length > 0 && (
        <p className="form-hint" style={{ color: 'var(--oai-warning, #fbbf24)' }}>
          Missing: {source.missing_fields.join(', ')}
        </p>
      )}
      {source.last_probe_message && <p className="form-hint">{source.last_probe_message}</p>}
      {source.completeness === 'incomplete' && (
        <div className="form-grid" style={{ marginTop: 12 }}>
          <input
            className="oai-input"
            placeholder="Region"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
          />
          <input
            className="oai-input"
            placeholder="Endpoint"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
          />
          <input
            className="oai-input"
            placeholder="Project"
            value={project}
            onChange={(e) => setProject(e.target.value)}
          />
          <button
            type="button"
            disabled={busy}
            className="oai-button"
            onClick={() =>
              run(() =>
                patchCloudCredential(source.provider, {
                  ...(region ? { region } : {}),
                  ...(endpoint ? { endpoint } : {}),
                  ...(project ? { project } : {}),
                }),
              )
            }
          >
            Save fields
          </button>
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
        <button
          type="button"
          disabled={busy || source.completeness !== 'complete'}
          className="oai-button oai-button-primary"
          onClick={() => run(() => approveCloudCredential(source.provider))}
        >
          Approve
        </button>
        <button
          type="button"
          disabled={busy}
          className="oai-button oai-button-secondary"
          onClick={() => run(() => rejectCloudCredential(source.provider))}
        >
          Reject
        </button>
        <button
          type="button"
          disabled={busy || source.status !== 'approved'}
          className="oai-button confirm-delete-btn"
          onClick={() => run(() => revokeCloudCredential(source.provider))}
        >
          Revoke
        </button>
      </div>
      {error && <p className="oai-error">{error}</p>}
    </div>
  );
}

export default function CloudCredentialsPage() {
  const qc = useQueryClient();
  const { data: session, isSuccess: sessionReady } = useQuery({
    queryKey: ['session'],
    queryFn: fetchSession,
  });
  const allowed = canManageModels(session?.permissions);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['cloud-credentials'],
    queryFn: () => fetchCloudCredentials(true),
    enabled: sessionReady && allowed,
  });

  const rescan = useMutation({
    mutationFn: rescanCloudCredentials,
    onSuccess: (result) => {
      qc.setQueryData(['cloud-credentials'], result);
    },
  });

  const scanning = isLoading || rescan.isPending;
  const sources = data?.sources ?? [];
  const emptyDetection =
    !scanning && !isError && data != null && noProvidersDetected(sources);

  if (!allowed) {
    return <p className="form-hint">{modelsAccessDeniedMessage()}</p>;
  }

  if (scanning) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
        <p className="form-hint" style={{ marginTop: 12 }}>
          Scanning for ambient cloud LLM credentials…
        </p>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <h2 className="oai-subsection-title">Cloud credentials</h2>
          <p className="form-hint">
            Detect ambient AWS, Microsoft Foundry, and GCP credentials. Approve before agents use
            platform-supplied models.
          </p>
        </div>
        <button
          type="button"
          className="oai-button"
          disabled={rescan.isPending}
          onClick={() => rescan.mutate()}
        >
          Rescan
        </button>
      </div>
      {isError && (
        <div className="oai-card" style={{ borderColor: 'var(--oai-danger, #f87171)' }}>
          <p className="oai-error" style={{ marginBottom: 8 }}>
            {error instanceof Error ? error.message : 'Failed to load cloud credentials'}
          </p>
          <button type="button" className="oai-button" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      )}
      {emptyDetection && (
        <div className="oai-card">
          <p className="form-hint" style={{ marginBottom: 8 }}>
            No hosted cloud LLM credentials were detected on this environment. Ambient AWS,
            Microsoft Foundry, and GCP configuration was not found via environment variables or
            instance metadata.
          </p>
          <p className="form-hint">
            You can still add models under{' '}
            <Link href="/settings/models" style={{ color: 'var(--oai-primary)' }}>
              LLM models
            </Link>{' '}
            using bring-your-own-key API credentials.
          </p>
        </div>
      )}
      {data?.platform_model && (
        <div className="oai-card">
          <p className="form-hint">
            Platform model: {data.platform_model.model_id} ({data.platform_model.provider})
            {data.platform_model.available
              ? ' — available to agent'
              : ' — approve + validate + enable in LLM models'}
          </p>
        </div>
      )}
      <div style={{ display: 'grid', gap: 0 }}>
        {sources.map((source) => (
          <SourceCard key={source.provider} source={source} onRefresh={() => refetch()} />
        ))}
      </div>
    </div>
  );
}
