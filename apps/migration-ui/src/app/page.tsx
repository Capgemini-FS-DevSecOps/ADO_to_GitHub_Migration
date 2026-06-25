'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { ChartBarIcon } from '@/components/Icons';
import { RunStepDetails } from '@/components/RunStepDetails';
import { StepPipelineBar, StepStatusBadge } from '@/components/PipelineProgress';
import { ACCEL, cancelPipelineRun, fetchDashboard, fetchPipelineRun, fetchPipelineRuns } from '@/lib/api';
import { mapPipelineRunStatus, pipelineRunNeedsApproval } from '@/lib/pipelineRunStatus';
import type { PipelineRun, StepStatus } from '@/lib/types';

function migrationStatus(s: string): StepStatus {
  return mapPipelineRunStatus(s);
}

function formatStartedAt(iso: string): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

type RunFilter = 'all' | 'active' | 'completed' | 'awaiting_approval' | 'failed';

const FILTER_LABELS: Record<RunFilter, string> = {
  all: 'All',
  active: 'In Progress',
  completed: 'Completed',
  awaiting_approval: 'Needs Approval',
  failed: 'Failed',
};

const PAGE_SIZE = 10;

export default function DashboardPage() {
  const params = useSearchParams();
  const initialId = params.get('id') ?? params.get('run');
  const [selectedId, setSelectedId] = useState<string | null>(initialId);
  const [page, setPage] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<RunFilter>('all');

  const { data: dashData, isLoading: dashLoading, error: dashError } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: (query) => {
      const active = query.state.data?.active_migrations?.length ?? 0;
      return active > 0 ? 5000 : 15000;
    },
  });

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

  const allRuns = listData?.runs ?? [];
  const total = listData?.total ?? 0;
  const summary = listData?.summary;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const run: PipelineRun | undefined = detailData?.run;

  // Client-side search + filter on the current page of runs
  const filteredRuns = useMemo(() => {
    let r = allRuns;
    if (statusFilter !== 'all') {
      r = r.filter((x) => {
        const s = mapPipelineRunStatus(x.status);
        if (statusFilter === 'active') return s === 'running' || s === 'pending';
        if (statusFilter === 'completed') return s === 'completed' || s === 'dry_run_complete';
        if (statusFilter === 'awaiting_approval') return s === 'awaiting_approval';
        if (statusFilter === 'failed') return s === 'failed';
        return true;
      });
    }
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      r = r.filter(
        (x) =>
          x.name.toLowerCase().includes(q) ||
          x.phase.toLowerCase().includes(q) ||
          (x.started_by_label ?? '').toLowerCase().includes(q),
      );
    }
    return r;
  }, [allRuns, statusFilter, searchQuery]);

  if (dashLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
        <p>Loading dashboard…</p>
      </div>
    );
  }

  if (dashError) {
    const message = dashError instanceof Error ? dashError.message : 'Request failed';
    const unreachable = message.includes('Cannot reach Accelerator API');
    const needsAuth = message.includes('Not authenticated');

    return (
      <div className="oai-error">
        {unreachable ? (
          <>
            <p>
              Cannot reach Accelerator API at <code>{ACCEL}</code>.
            </p>
            <ul className="dashboard-error-steps">
              <li>Confirm containers are running: <code>docker compose ps</code></li>
              <li>Restart stack: <code>docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build</code></li>
              <li>Or locally: <code>.\scripts\dev\run-local-agent.ps1</code> and <code>.\scripts\dev\run-ui.ps1</code></li>
              <li>Test: <code>curl {ACCEL}/health</code></li>
            </ul>
          </>
        ) : needsAuth ? (
          <p>Sign in required to view the dashboard.</p>
        ) : (
          <p>{message}</p>
        )}
        <p>
          <Link href="/login?bootstrap=1">Create admin account</Link>
          {' · '}
          <Link href="/login">Sign in</Link>
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="oai-page-title">Migration Dashboard</h1>

      {/* Progress Overview */}
      <div className="metrics-section">
        <div className="section-header">
          <span className="section-icon-svg"><ChartBarIcon size={24} color="#35b8ff" /></span>
          <h2 className="section-title">Progress Overview</h2>
        </div>
        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-label">Repos completed</div>
            <div className="metric-value">{dashData?.completed_repos ?? 0}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Repos failed</div>
            <div className="metric-value badge-manual">{dashData?.failed_repos ?? 0}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Pipelines inventoried</div>
            <div className="metric-value">{dashData?.inventory_count ?? 0}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Pipeline migrations</div>
            <div className="metric-value">{dashData?.total_pipelines ?? 0}</div>
          </div>
        </div>
      </div>

      {/* Pipeline Run Stats */}
      <div className="stats-row" style={{ marginTop: '1rem' }}>
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

      {/* Active Migrations from Dashboard API */}
      {(dashData?.active_migrations?.length ?? 0) > 0 && dashData && (
        <div className="oai-card" style={{ marginTop: '1rem' }}>
          <h2 className="oai-subsection-title">Active migrations</h2>
          <div className="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Status</th>
                  <th>Current step</th>
                  <th>Started by</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {dashData.active_migrations!.map((m) => (
                  <tr key={m.id}>
                    <td>
                      <Link href={`/?run=${m.id}`}>{m.name}</Link>
                      {!m.repository_id && m.phase && (
                        <span style={{ marginLeft: 8, fontSize: 11, color: '#888' }}>
                          {m.phase.toUpperCase()}{m.wave_id != null ? ` / wave ${m.wave_id}` : ''}
                        </span>
                      )}
                      {m.dry_run && (
                        <span style={{ marginLeft: 8, fontSize: 11, color: '#888' }}>dry-run</span>
                      )}
                    </td>
                    <td>
                      <StepStatusBadge status={migrationStatus(m.status)} />
                    </td>
                    <td>{m.current_step || '—'}</td>
                    <td>{m.started_by_display_name || m.started_by_username || '—'}</td>
                    <td>{formatStartedAt(m.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pipeline Runs Monitor */}
      <div className="oai-card" style={{ marginTop: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
          <h2 className="oai-subsection-title" style={{ margin: 0 }}>Pipeline Runs</h2>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            style={{ padding: '6px 12px', fontSize: 11 }}
            onClick={() => refetchList()}
          >
            Refresh
          </button>
        </div>

        {/* Search + Filter */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            className="oai-input"
            type="text"
            placeholder="Search by name, phase, or user…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ flex: 1, minWidth: 200, fontSize: 13 }}
          />
          <div style={{ display: 'flex', gap: 4 }}>
            {(Object.keys(FILTER_LABELS) as RunFilter[]).map((f) => (
              <button
                key={f}
                type="button"
                className={`oai-button ${statusFilter === f ? 'oai-button-primary' : 'oai-button-secondary'}`}
                style={{ padding: '4px 10px', fontSize: 11 }}
                onClick={() => setStatusFilter(f)}
              >
                {FILTER_LABELS[f]}
              </button>
            ))}
          </div>
        </div>

        <div className="content-layout">
          {/* Run List */}
          <div>
            {filteredRuns.map((r) => (
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
                  {r.dry_run ? 'DRY RUN' : 'LIVE'}{!r.repository_id ? ` · ${r.phase.toUpperCase()}` : ''} ·{' '}
                  {new Date(r.created_at).toLocaleString()}
                  {(() => {
                    const migrateStep = r.steps.find((s) => s.id === 'migrate_repos');
                    const repoDetails = (migrateStep?.result as Record<string, unknown> | undefined)?.repo_details as Array<Record<string, unknown>> | undefined;
                    const repoCount = repoDetails?.length ?? (migrateStep?.result as Record<string, unknown> | undefined)?.completed as number | undefined;
                    return repoCount != null ? ` · ${repoCount} repo${repoCount === 1 ? '' : 's'}` : '';
                  })()}
                </p>
              </button>
            ))}
            {!filteredRuns.length && (
              <div className="oai-card oai-welcome-card">
                <p>
                  No runs match{searchQuery ? ` "${searchQuery}"` : ''}.{' '}
                  <a href="/settings/migrate">Start a migration</a>.
                </p>
              </div>
            )}

            {/* Pagination */}
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

          {/* Run Detail */}
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
                    {(() => {
                      const migrateStep = run.steps.find((s) => s.id === 'migrate_repos');
                      const repoDetails = (migrateStep?.result as Record<string, unknown> | undefined)?.repo_details as Array<Record<string, unknown>> | undefined;
                      const repoCount = repoDetails?.length ?? (migrateStep?.result as Record<string, unknown> | undefined)?.completed as number | undefined;
                      return repoCount != null ? `${repoCount} repo${repoCount === 1 ? '' : 's'} · ` : '';
                    })()}
                    Started by{' '}
                    <strong>{run.started_by_label || run.started_by_display_name || run.started_by_username || 'System'}</strong>
                    {run.approved_by_label && run.approved_by_label !== 'Not required (dry run)' && (
                      <>{' · '}Approved by <strong>{run.approved_by_label}</strong></>
                    )}
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
                            <td style={{ whiteSpace: 'pre-wrap' }}>{s.message || '—'}</td>
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

      {/* Phase Gates */}
      {(dashData?.phase_gates?.length ?? 0) > 0 && dashData && (
        <div className="oai-card" style={{ marginTop: '1rem' }}>
          <h2 className="oai-subsection-title">Phase Gates</h2>
          <table>
            <thead>
              <tr>
                <th>Phase</th>
                <th>Status</th>
                <th>Repo %</th>
              </tr>
            </thead>
            <tbody>
              {dashData.phase_gates.map((g) => (
                <tr key={g.phase}>
                  <td>{g.phase}</td>
                  <td>{g.status}</td>
                  <td>{g.repo_success_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Quick Actions */}
      <div className="oai-card" style={{ marginTop: '1rem' }}>
        <h2 className="oai-subsection-title">Quick actions</h2>
        <p>
          <a href="/settings/profiles">Configure migration profiles</a> ·{' '}
          <a href="/settings/migrate">Start migration pipeline</a>
        </p>
      </div>
    </div>
  );
}
