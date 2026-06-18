'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { fetchPipelineRuns, fetchReadiness, fetchSettings } from '@/lib/api';

export default function WorkflowsPage() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const { data: readiness, isLoading: readinessLoading } = useQuery({
    queryKey: ['readiness'],
    queryFn: () => fetchReadiness(),
    retry: false,
  });
  const { data: runsData, isLoading: runsLoading } = useQuery({
    queryKey: ['pipeline-runs'],
    queryFn: () => fetchPipelineRuns(),
  });

  const runs = runsData?.runs ?? [];
  const profileId = settings?.active_profile_id;
  const active = settings?.migration_profiles?.find((p) => p.id === profileId);

  return (
    <div>
      <h1 className="oai-page-title">Workflow Review</h1>
      <p>
        Pipeline conversion readiness and recent migration runs that include workflow transformation.
      </p>

      {profileId && active && (
        <p className="form-hint" style={{ marginBottom: 16 }}>
          Active profile: <strong>{active.name}</strong>
        </p>
      )}

      <div className="oai-card" style={{ marginBottom: '1rem' }}>
        <h2 className="oai-subsection-title">Pipeline readiness</h2>
        {readinessLoading && <p className="oai-loading">Loading inventory…</p>}
        {!readinessLoading && readiness && (
          <div className="metrics-grid">
            <div className="metric-card">
              <div className="metric-label">Auto-convert</div>
              <div className="metric-value badge-auto">{readiness.auto ?? 0}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Assisted</div>
              <div className="metric-value badge-assisted">{readiness.assisted ?? 0}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Manual</div>
              <div className="metric-value badge-manual">{readiness.manual ?? 0}</div>
            </div>
          </div>
        )}
        {!readinessLoading && !readiness && (
          <p>
            No pipeline inventory yet. Run a full pipeline scan from{' '}
            <Link href="/migrate">Migrate</Link> (include inventory step) or{' '}
            <code>ado2gh pipelines inventory</code>.
          </p>
        )}
      </div>

      <div className="oai-card" style={{ marginBottom: '1rem' }}>
        <h2 className="oai-subsection-title">Generated workflow output</h2>
        <p>
          Converted GitHub Actions YAML is written under{' '}
          <code>output/workflows/&lt;org&gt;/&lt;repo&gt;/.github/workflows/</code> on the accelerator
          host (or shared <code>ADO2GH_DATA_DIR</code> volume in Docker).
        </p>
        <p>Compare ADO source YAML with generated GHA before approving push-workflows.</p>
      </div>

      <div className="oai-card">
        <h2 className="oai-subsection-title">Recent pipeline runs</h2>
        {runsLoading && <p className="oai-loading">Loading runs…</p>}
        {!runsLoading && runs.length === 0 && (
          <p>
            No runs yet. Start one from <Link href="/migrate">Migrate</Link> or monitor on{' '}
            <Link href="/runs">Runs</Link>.
          </p>
        )}
        {!runsLoading && runs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phase</th>
                <th>Status</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.slice(0, 20).map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link href={`/runs?id=${r.id}`}>{r.name}</Link>
                  </td>
                  <td>{r.phase}</td>
                  <td>{r.status}</td>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
