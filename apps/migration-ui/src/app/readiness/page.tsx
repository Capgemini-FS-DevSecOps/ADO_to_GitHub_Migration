'use client';

import { useQuery } from '@tanstack/react-query';
import { fetchReadiness } from '@/lib/api';

export default function ReadinessPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['readiness'],
    queryFn: fetchReadiness,
  });

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
        <p>Loading readiness…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="oai-error">
        Readiness unavailable — run <code>ado2gh pipelines inventory</code> first.
      </div>
    );
  }

  return (
    <div>
      <h1 className="oai-page-title">Pipeline Readiness</h1>
      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-label">Auto</div>
          <div className="metric-value badge-auto">{data?.auto ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Assisted</div>
          <div className="metric-value badge-assisted">{data?.assisted ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Manual</div>
          <div className="metric-value badge-manual">{data?.manual ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Est. effort</div>
          <div className="metric-value">{data?.total_effort_hours?.toFixed?.(0) ?? 0}h</div>
        </div>
      </div>
      <div className="oai-card">
        <h2 className="oai-subsection-title">Pipeline breakdown</h2>
        <table>
          <thead>
            <tr>
              <th>Pipeline</th>
              <th>Class</th>
              <th>Hours</th>
              <th>SC</th>
            </tr>
          </thead>
          <tbody>
            {(data?.pipelines ?? []).slice(0, 50).map((p) => (
              <tr key={`${p.project}-${p.pipeline_id}`}>
                <td>{p.pipeline_name}</td>
                <td className={`badge-${p.classification}`}>{p.classification}</td>
                <td>{p.effort_hours}</td>
                <td>{p.service_connections}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
