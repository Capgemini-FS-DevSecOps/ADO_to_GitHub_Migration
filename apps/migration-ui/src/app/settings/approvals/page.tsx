'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  approveLiveExecution,
  denyLiveExecution,
  fetchLiveApprovals,
  type LiveApprovalItem,
} from '@/lib/api';
import { fetchSession } from '@/lib/auth';

export default function LiveApprovalsPage() {
  const qc = useQueryClient();
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const canApprove = session?.permissions?.can_approve_live_execution === true;
  const [reason, setReason] = useState('');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [mode, setMode] = useState<'approve' | 'deny' | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['live-approvals'],
    queryFn: () => fetchLiveApprovals('pending'),
    enabled: canApprove,
    refetchInterval: 10_000,
  });

  const decideMut = useMutation({
    mutationFn: async ({ id, approve }: { id: string; approve: boolean }) => {
      if (!reason.trim()) throw new Error('Reason is required');
      if (approve) return approveLiveExecution(id, reason);
      return denyLiveExecution(id, reason);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live-approvals'] });
      setActiveId(null);
      setMode(null);
      setReason('');
    },
  });

  if (!canApprove) {
    return (
      <p className="form-hint">
        Live execution approvals are visible to platform admins and approvers only.
      </p>
    );
  }

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  const approvals = data?.approvals ?? [];

  return (
    <div>
      <div className="settings-list-header">
        <div>
          <h2 className="oai-subsection-title" style={{ margin: 0 }}>
            Live execution approvals
          </h2>
          <p className="form-hint" style={{ margin: '4px 0 0' }}>
            Unified queue for agent sessions, dashboard migrate, and pipeline live runs.
          </p>
        </div>
        <button type="button" className="oai-button oai-button-secondary" onClick={() => refetch()}>
          Refresh
        </button>
      </div>

      {!approvals.length && (
        <div className="oai-card oai-welcome-card">
          <p>No pending live execution requests.</p>
        </div>
      )}

      {approvals.map((a: LiveApprovalItem) => (
        <div key={a.id} className="oai-card credential-card" style={{ marginBottom: 12 }}>
          <strong>{a.scope_type}</strong>
          <span className="step-badge" style={{ marginLeft: 8 }}>{a.status}</span>
          <p className="credential-meta">Requester: {a.requester_username}</p>
          <p className="credential-meta">Scope: {a.scope_id}</p>
          {a.reason_request && <p className="form-hint">{a.reason_request}</p>}
          {activeId === a.id ? (
            <div style={{ marginTop: 12 }}>
              <textarea
                className="oai-input"
                rows={2}
                placeholder="Reason (required)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button
                  type="button"
                  className="oai-button oai-button-primary"
                  disabled={decideMut.isPending || !reason.trim()}
                  onClick={() => decideMut.mutate({ id: a.id, approve: mode === 'approve' })}
                >
                  Confirm {mode}
                </button>
                <button
                  type="button"
                  className="oai-button oai-button-secondary"
                  onClick={() => {
                    setActiveId(null);
                    setMode(null);
                    setReason('');
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button
                type="button"
                className="oai-button oai-button-primary"
                onClick={() => {
                  setActiveId(a.id);
                  setMode('approve');
                }}
              >
                Approve
              </button>
              <button
                type="button"
                className="oai-button oai-button-secondary"
                onClick={() => {
                  setActiveId(a.id);
                  setMode('deny');
                }}
              >
                Deny
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
