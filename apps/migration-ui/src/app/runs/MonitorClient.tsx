'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { RunStepDetails } from '@/components/RunStepDetails';
import { StepPipelineBar, StepStatusBadge } from '@/components/PipelineProgress';
import { cancelPipelineRun, fetchPipelineRun, fetchPipelineRuns } from '@/lib/api';
import { mapPipelineRunStatus, pipelineRunNeedsApproval } from '@/lib/pipelineRunStatus';
import type { PipelineRun } from '@/lib/types';

const PAGE_SIZE = 10;

export default function MonitorClient() {
  const params = useSearchParams();
  const initialId = params.get('id');
  const [selectedId, setSelectedId] = useState<string | null>(initialId);
  const [page, setPage] = useState(0);

  const { data: listData, refetch: refetchList } = useQuery({
    queryKey: ['pipeline-runs', page],
    queryFn: () => fetchPipelineRuns({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    refetchInterval: 3000,
  });

  const { data: detailData, refetch: refetchDetail } = useQuery({
    queryKey: ['pipeline-run', selectedId],
    queryFn: () => fetchPipelineRun(selectedId!),
    enabled: !!selectedId,
    refetchInterval: 2000,
  });

  const cancelMut = useMutation({
    mutationFn: () => cancelPipelineRun(selectedId!),
    onSuccess: () => {
      refetchList();
      refetchDetail();
    },
  });

  useEffect(() => {
    if (initialId) setSelectedId(initialId);
  }, [initialId]);

  const runs = listData?.runs ?? [];
  const total = listData?.total ?? 0;
  const summary = listData?.summary;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const run: PipelineRun | undefined = detailData?.run;

  return (
    <div>
      <h1 className="oai-page-title">Migration Monitor</h1>

      <div className="stats-row">
        <div className="stat-pill">
          <div className="stat-pill-value">{summary?.total ?? total}</div>
          <div className="stat-pill-label">Total runs</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value badge-auto">{summary?.completed_live ?? 0}</div>
          <div className="stat-pill-label">Completed (live)</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value">{summary?.dry_run ?? 0}</div>
          <div className="stat-pill-label">Dry runs</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value">{summary?.active ?? 0}</div>
          <div className="stat-pill-label">Active</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value badge-manual">{summary?.awaiting_approval ?? 0}</div>
          <div className="stat-pill-label">Needs approval</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value badge-manual">{summary?.failed ?? 0}</div>
          <div className="stat-pill-label">Failed</div>
        </div>
      </div>

      <div className="content-layout">
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <h2 className="oai-subsection-title" style={{ margin: 0 }}>Runs</h2>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              style={{ padding: '6px 12px', fontSize: 11 }}
              onClick={() => refetchList()}
            >
              Refresh
            </button>
          </div>
          {runs.map((r) => (
            <button
              key={r.id}
              type="button"
              className={`run-list-item${selectedId === r.id ? ' run-list-item-active' : ''}`}
              onClick={() => setSelectedId(r.id)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <strong>{r.name}</strong>
                <StepStatusBadge status={mapPipelineRunStatus(r.status)} />
              </div>
              <StepPipelineBar steps={r.steps} compact />
              <p style={{ fontSize: 11, margin: '8px 0 0', color: '#888' }}>
                {r.dry_run ? 'DRY RUN' : 'LIVE'} · {r.phase.toUpperCase()} ·{' '}
                {new Date(r.created_at).toLocaleString()}
                {r.started_by_label && (
                  <>
                    <br />
                    Started by {r.started_by_label}
                    {r.approved_by_label ? ` · Approved by ${r.approved_by_label}` : ''}
                  </>
                )}
              </p>
            </button>
          ))}
          {!runs.length && (
            <div className="oai-card oai-welcome-card">
              <p>
                No runs yet — start a pipeline from <a href="/migrate">Migrate</a>.
              </p>
            </div>
          )}
          {total > PAGE_SIZE && (
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginTop: 12,
                gap: 8,
              }}
            >
              <button
                type="button"
                className="oai-button oai-button-secondary"
                style={{ padding: '6px 12px', fontSize: 11 }}
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                Previous
              </button>
              <span style={{ fontSize: 12, color: '#888' }}>
                Page {page + 1} of {totalPages} ({total} runs)
              </span>
              <button
                type="button"
                className="oai-button oai-button-secondary"
                style={{ padding: '6px 12px', fontSize: 11 }}
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </div>

        <div>
          {run ? (
            <>
              <div className="oai-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                  <h2 className="oai-subsection-title" style={{ margin: 0 }}>{run.name}</h2>
                  {(run.status === 'running' || run.status === 'pending') && (
                    <button
                      type="button"
                      className="oai-button oai-button-secondary"
                      style={{ padding: '6px 14px', fontSize: 11, borderColor: '#ff6b6b', color: '#ff6b6b' }}
                      disabled={cancelMut.isPending}
                      onClick={() => cancelMut.mutate()}
                    >
                      {cancelMut.isPending ? 'Stopping…' : 'Stop migration'}
                    </button>
                  )}
                </div>
                <p>
                  Status: <StepStatusBadge status={mapPipelineRunStatus(run.status)} />
                  {pipelineRunNeedsApproval(run.status) && (
                    <span style={{ marginLeft: 8, fontSize: 12, color: '#ffc107' }}>
                      Live migration blocked until an admin or approver approves this run.{' '}
                      <a href="/settings/approvals">Review approvals</a>
                    </span>
                  )}
                  {run.dry_run && run.status === 'dry_run_complete' && (
                    <span style={{ marginLeft: 8, fontSize: 12, color: '#888' }}>
                      (preview only — not recorded as a migration)
                    </span>
                  )}
                  {run.error && (
                    <pre
                      className="run-error-detail"
                      style={{ margin: '12px 0 0', fontSize: 11 }}
                    >
                      {run.error}
                    </pre>
                  )}
                </p>
                <p className="form-hint" style={{ margin: '8px 0 0' }}>
                  Started by{' '}
                  <strong>{run.started_by_label || run.started_by_display_name || run.started_by_username || 'Unknown'}</strong>
                  {' · '}
                  Approved by{' '}
                  <strong>{run.approved_by_label || '—'}</strong>
                </p>
                <StepPipelineBar steps={run.steps} />
                <div className="table-responsive">
                <table style={{ marginTop: 20 }}>
                  <thead>
                    <tr>
                      <th>Step</th>
                      <th>Status</th>
                      <th>Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.steps.map((s) => (
                      <tr key={s.id}>
                        <td>{s.label}</td>
                        <td>
                          <StepStatusBadge status={s.status} />
                        </td>
                        <td>{s.message || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              </div>
              <RunStepDetails steps={run.steps} />
              <div className="oai-card">
                <h2 className="oai-subsection-title">Logs</h2>
                <div className="log-terminal">
                  {(run.logs ?? []).join('\n') || 'Waiting for log output…'}
                </div>
              </div>
            </>
          ) : (
            <div className="oai-card oai-welcome-card">
              <p>Select a run to view pipeline progress and logs.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
