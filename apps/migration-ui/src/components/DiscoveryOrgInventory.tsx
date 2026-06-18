'use client';

import type { ScanProjectDetail } from '@/lib/types';

type OrgInventory = {
  total_service_connections?: number;
  total_variable_groups?: number;
  total_environments?: number;
  total_pipeline_stubs?: number;
  pipeline_inventory_count?: number;
  total_pipelines_indexed?: number;
};

export function DiscoveryOrgInventory({
  orgInventory,
  projectDetails,
  pipelineInventoryCount,
}: {
  orgInventory?: OrgInventory;
  projectDetails?: ScanProjectDetail[];
  pipelineInventoryCount?: number;
}) {
  const indexed =
    pipelineInventoryCount ??
    orgInventory?.pipeline_inventory_count ??
    orgInventory?.total_pipelines_indexed ??
    0;

  if (!orgInventory && !projectDetails?.length) return null;

  return (
    <div className="discovery-org-inventory">
      <h2 className="oai-subsection-title">ADO environment inventory</h2>
      <p className="form-hint">
        Full scan includes repositories, pipelines, service connections, variable groups, and
        environments. Pipeline inventory powers conversion and secrets mapping.
      </p>
      <div className="metrics-grid" style={{ marginBottom: 16 }}>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_service_connections ?? 0}</span>
          <span className="metric-label">Service connections</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_variable_groups ?? 0}</span>
          <span className="metric-label">Variable groups</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_environments ?? 0}</span>
          <span className="metric-label">Environments</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{indexed}</span>
          <span className="metric-label">Pipelines indexed</span>
        </div>
      </div>

      {(projectDetails?.length ?? 0) > 0 && (
        <div className="discovery-table-scroll">
          <table className="discovery-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Service connections</th>
                <th>Variable groups</th>
                <th>Environments</th>
                <th>Pipelines</th>
              </tr>
            </thead>
            <tbody>
              {projectDetails!.map((p) => (
                <tr key={p.project_id || p.project}>
                  <td>{p.project}</td>
                  <td>{p.service_connection_count ?? 0}</td>
                  <td>{p.variable_group_count ?? 0}</td>
                  <td>{p.environment_count ?? 0}</td>
                  <td>{p.pipeline_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(projectDetails ?? []).some((p) => (p.service_connections?.length ?? 0) > 0) && (
        <details style={{ marginTop: 16 }}>
          <summary className="form-hint">Service connection names by project</summary>
          {(projectDetails ?? []).map((p) =>
            (p.service_connections?.length ?? 0) > 0 ? (
              <div key={`sc-${p.project}`} style={{ marginTop: 8 }}>
                <strong>{p.project}</strong>
                <ul>
                  {p.service_connections!.map((sc) => (
                    <li key={`${p.project}-${sc.name}`}>
                      {sc.name}
                      {sc.type ? ` (${sc.type})` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </details>
      )}
    </div>
  );
}
