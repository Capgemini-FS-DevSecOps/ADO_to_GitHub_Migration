'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { RunStepDetails } from '@/components/RunStepDetails';
import { StepPipelineBar, StepStatusBadge } from '@/components/PipelineProgress';
import { cancelPipelineRun, fetchPipelineRun, fetchPipelineRuns } from '@/lib/api';
import type { PipelineRun, StepStatus } from '@/lib/types';

function runStatus(s: string): StepStatus {
  if (s === 'running') return 'running';
  if (s === 'completed') return 'completed';
  if (s === 'failed') return 'failed';
  if (s === 'cancelled') return 'skipped';
  return 'pending';
}

export default function MonitorClient() {
  const params = useSearchParams();
  const initialId = params.get('id');
  const [selectedId, setSelectedId] = useState<string | null>(initialId);

  const { data: listData, refetch: refetchList } = useQuery({
    queryKey: ['pipeline-runs'],
    queryFn: fetchPipelineRuns,
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
  const run: PipelineRun | undefined = detailData?.run;
  const completed = runs.filter((r) => r.status === 'completed').length;
  const failed = runs.filter((r) => r.status === 'failed').length;
  const active = runs.filter((r) => r.status === 'running').length;

  return (
    <div>
      <h1 className="oai-page-title">Migration Monitor</h1>

      <div className="stats-row">
        <div className="stat-pill">
          <div className="stat-pill-value">{runs.length}</div>
          <div className="stat-pill-label">Total runs</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value badge-auto">{completed}</div>
          <div className="stat-pill-label">Completed</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value">{active}</div>
          <div className="stat-pill-label">Active</div>
        </div>
        <div className="stat-pill">
          <div className="stat-pill-value badge-manual">{failed}</div>
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
                <StepStatusBadge status={runStatus(r.status)} />
              </div>
              <StepPipelineBar steps={r.steps} compact />
              <p style={{ fontSize: 11, margin: '8px 0 0', color: '#888' }}>
                {r.dry_run ? 'DRY RUN' : 'LIVE'} · {r.phase.toUpperCase()} ·{' '}
                {new Date(r.created_at).toLocaleString()}
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
                  Status: <StepStatusBadge status={runStatus(run.status)} />
                  {run.error && (
                    <pre
                      className="run-error-detail"
                      style={{ margin: '12px 0 0', fontSize: 11 }}
                    >
                      {run.error}
                    </pre>
                  )}
                </p>
                <StepPipelineBar steps={run.steps} />
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
