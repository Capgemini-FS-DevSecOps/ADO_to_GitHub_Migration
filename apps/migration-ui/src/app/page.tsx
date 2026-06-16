'use client';

import { useQuery } from '@tanstack/react-query';
import { ChartBarIcon } from '@/components/Icons';
import { fetchDashboard } from '@/lib/api';

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 15000,
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
    return (
      <div className="oai-error">
        Cannot reach Accelerator API — ensure the accelerator is running on port 8080
        (<code>docker compose up</code> or <code>.\scripts\run-local.ps1</code>).
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
