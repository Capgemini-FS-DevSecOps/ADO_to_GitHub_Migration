'use client';

import type { ScanProjectDetail } from '@/lib/types';

export function ScanDiagnostics({
  projectsScanned,
  reposScanned,
  projectDetails,
  warnings,
  status,
}: {
  projectsScanned: number;
  reposScanned: number;
  projectDetails?: ScanProjectDetail[];
  warnings?: string[];
  status?: string;
}) {
  const empty = status === 'empty' || (projectsScanned > 0 && reposScanned === 0);

  return (
    <div className="scan-diagnostics">
      <div className="scan-summary-row">
        <span>{projectsScanned} project(s)</span>
        <span>{reposScanned} Git repo(s) found</span>
        {empty && <span className="scan-status-empty">No repos discovered</span>}
      </div>

      {(warnings?.length ?? 0) > 0 && (
        <div className="scan-warnings">
          {warnings!.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}

      {(projectDetails?.length ?? 0) > 0 && (
        <div className="discovery-table-scroll" style={{ marginTop: 12 }}>
          <table className="discovery-table scan-project-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Git repos</th>
                <th>Disabled</th>
                <th>Pipelines</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {projectDetails!.map((p) => (
                <tr key={p.project_id || p.project}>
                  <td>{p.project}</td>
                  <td>{p.repo_count}</td>
                  <td>{p.disabled_count ?? 0}</td>
                  <td>{p.pipeline_count ?? 0}</td>
                  <td className={p.error ? 'badge-manual' : ''}>
                    {p.error ? p.error : p.repo_count ? 'OK' : 'No Git repos'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
