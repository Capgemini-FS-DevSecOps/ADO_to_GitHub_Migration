'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { ChartBarIcon } from '@/components/Icons';
import { StepStatusBadge } from '@/components/PipelineProgress';
import { fetchDashboard, ACCEL } from '@/lib/api';
import { mapPipelineRunStatus } from '@/lib/pipelineRunStatus';
import type { StepStatus } from '@/lib/types';

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

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: (query) => {
      const active = query.state.data?.active_migrations?.length ?? 0;
      return active > 0 ? 5000 : 15000;
    },
  });

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
        <p>Loading dashboard…</p>
      </div>
    );
  }

  if (error) {
    const message = error instanceof Error ? error.message : 'Request failed';
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
              <li>Or locally: <code>.\scripts\run-local.ps1</code></li>
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
      <div className="metrics-section">
        <div className="section-header">
          <span className="section-icon-svg"><ChartBarIcon size={24} color="#35b8ff" /></span>
          <h2 className="section-title">Progress Overview</h2>
        </div>
        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-label">Repos completed</div>
            <div className="metric-value">{data?.completed_repos ?? 0}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Repos failed</div>
            <div className="metric-value badge-manual">{data?.failed_repos ?? 0}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Pipelines inventoried</div>
            <div className="metric-value">{data?.inventory_count ?? 0}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Pipeline migrations</div>
            <div className="metric-value">{data?.total_pipelines ?? 0}</div>
          </div>
        </div>
      </div>

      {(data?.active_migrations?.length ?? 0) > 0 && data && (
        <div className="oai-card" style={{ marginTop: '1rem' }}>
          <h2 className="oai-subsection-title">Active migrations</h2>
          <div className="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Status</th>
                  <th>Phase</th>
                  <th>Current step</th>
                  <th>Started by</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {data.active_migrations!.map((m) => (
                  <tr key={m.id}>
                    <td>
                      <Link href={`/runs?id=${m.id}`}>{m.name}</Link>
                      {m.dry_run && (
                        <span style={{ marginLeft: 8, fontSize: 11, color: '#888' }}>dry-run</span>
                      )}
                    </td>
                    <td>
                      <StepStatusBadge status={migrationStatus(m.status)} />
                    </td>
                    <td>{m.phase}{m.wave_id != null ? ` / wave ${m.wave_id}` : ''}</td>
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

      {(data?.phase_gates?.length ?? 0) > 0 && data && (
        <div className="oai-card">
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
              {data.phase_gates.map((g) => (
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

      <div className="oai-card" style={{ marginTop: '1rem' }}>
        <h2 className="oai-subsection-title">Quick actions</h2>
        <p>
          <a href="/settings/profiles">Configure migration profiles</a> ·{' '}
          <a href="/migrate">Start migration pipeline</a> ·{' '}
          <a href="/runs">Monitor runs</a>
        </p>
      </div>
    </div>
  );
}
