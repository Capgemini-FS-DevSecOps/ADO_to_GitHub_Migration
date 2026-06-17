'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { approveProfile, denyProfile, fetchPendingProfiles } from '@/lib/api';

export default function PendingProfilesPage() {
  const qc = useQueryClient();
  const { data: pending, isLoading } = useQuery({
    queryKey: ['pending-profiles'],
    queryFn: fetchPendingProfiles,
  });

  const approveMut = useMutation({
    mutationFn: approveProfile,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pending-profiles', 'settings'] }),
  });

  const denyMut = useMutation({
    mutationFn: (id: string) => denyProfile(id, 'Denied by admin'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pending-profiles', 'settings'] }),
  });

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  return (
    <div>
      <div className="settings-list-header">
        <h2 className="oai-subsection-title" style={{ margin: 0 }}>Pending profile approval</h2>
        <Link href="/settings/profiles" className="oai-button oai-button-secondary">Back</Link>
      </div>
      {(pending ?? []).map((p) => (
        <div key={p.id} className="oai-card credential-card">
          <strong>{p.name}</strong>
          <p className="credential-meta">Submitted by {p.submitted_by || '—'}</p>
          <p className="credential-meta">{p.ado_org_url} → {p.gh_org}</p>
          <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="oai-button oai-button-primary"
              disabled={approveMut.isPending}
              onClick={() => approveMut.mutate(p.id)}
            >
              Approve
            </button>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={denyMut.isPending}
              onClick={() => denyMut.mutate(p.id)}
            >
              Deny
            </button>
          </div>
        </div>
      ))}
      {!pending?.length && <p className="form-hint">No profiles awaiting approval.</p>}
    </div>
  );
}
