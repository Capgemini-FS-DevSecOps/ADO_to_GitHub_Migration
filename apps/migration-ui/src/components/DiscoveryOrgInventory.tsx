'use client';

import type { ScanProjectDetail } from '@/lib/types';

type OrgInventory = {
  total_service_connections?: number;
  total_variable_groups?: number;
  total_environments?: number;
  total_pipeline_stubs?: number;
  pipeline_inventory_count?: number;
  total_pipelines_indexed?: number;
  total_artifact_feeds?: number;
  total_teams?: number;
  total_iterations?: number;
  total_work_items?: number;
  total_work_item_types?: number;
  total_test_plans?: number;
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
        Full scan includes repositories, pipelines, service connections, variable groups,
        environments, artifacts, Azure Boards (work items, teams, iterations), and test plans.
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
          <span className="metric-value">{orgInventory?.total_artifact_feeds ?? 0}</span>
          <span className="metric-label">Artifact feeds</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_work_items ?? 0}</span>
          <span className="metric-label">Work items</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_teams ?? 0}</span>
          <span className="metric-label">Teams</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_iterations ?? 0}</span>
          <span className="metric-label">Iterations</span>
        </div>
        <div className="metric-card">
          <span className="metric-value">{orgInventory?.total_test_plans ?? 0}</span>
          <span className="metric-label">Test plans</span>
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
                <th>Repos</th>
                <th>Pipelines</th>
                <th>SC</th>
                <th>VG</th>
                <th>Env</th>
                <th>Artifacts</th>
                <th>Work items</th>
                <th>Teams</th>
                <th>Test plans</th>
              </tr>
            </thead>
            <tbody>
              {projectDetails!.map((p) => (
                <tr key={p.project_id || p.project}>
                  <td>{p.project}</td>
                  <td>{p.repo_count ?? 0}</td>
                  <td>{p.pipeline_count ?? 0}</td>
                  <td>{p.service_connection_count ?? 0}</td>
                  <td>{p.variable_group_count ?? 0}</td>
                  <td>{p.environment_count ?? 0}</td>
                  <td>{p.artifact_feed_count ?? 0}</td>
                  <td>{p.work_item_count ?? 0}</td>
                  <td>{p.team_count ?? 0}</td>
                  <td>{p.test_plan_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(projectDetails ?? []).some((p) => (p.service_connections?.length ?? 0) > 0) && (
        <details className="discovery-details-group" style={{ marginTop: 16 }}>
          <summary className="form-hint">Service connection names by project</summary>
          {(projectDetails ?? []).map((p) =>
            (p.service_connections?.length ?? 0) > 0 ? (
              <div key={`sc-${p.project}`} className="discovery-details-section">
                <div className="discovery-details-title">{p.project}</div>
                <div className="discovery-details-list">
                  {p.service_connections!.map((sc) => (
                    <div key={`${p.project}-${sc.name}`} className="discovery-details-row">
                      <span className="discovery-details-name">{sc.name}</span>
                      {sc.type ? (
                        <span className="discovery-details-badge">{sc.type}</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null,
          )}
        </details>
      )}

      {(projectDetails ?? []).some((p) => (p.artifact_feeds?.length ?? 0) > 0) && (
        <details className="discovery-details-group" style={{ marginTop: 16 }}>
          <summary className="form-hint">Artifact feeds by project</summary>
          {(projectDetails ?? []).map((p) =>
            (p.artifact_feeds?.length ?? 0) > 0 ? (
              <div key={`af-${p.project}`} className="discovery-details-section">
                <div className="discovery-details-title">{p.project}</div>
                <div className="discovery-details-list">
                  {p.artifact_feeds!.map((af) => (
                    <div key={`${p.project}-${af.name}`} className="discovery-details-row">
                      <span className="discovery-details-name">{af.name}</span>
                      <span className="discovery-details-badge">
                        {af.is_public ? 'public' : 'private'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null,
          )}
        </details>
      )}

      {(projectDetails ?? []).some((p) => (p.teams?.length ?? 0) > 0 || (p.work_item_types?.length ?? 0) > 0) && (
        <details className="discovery-details-group" style={{ marginTop: 16 }}>
          <summary className="form-hint">Azure Boards: teams &amp; work item types by project</summary>
          {(projectDetails ?? []).map((p) =>
            (p.teams?.length ?? 0) > 0 || (p.work_item_types?.length ?? 0) > 0 ? (
              <div key={`boards-${p.project}`} className="discovery-details-section">
                <div className="discovery-details-title">{p.project}</div>
                {p.teams && p.teams.length > 0 && (
                  <div className="discovery-details-line">
                    <span className="form-hint">Teams ({p.teams.length}): </span>
                    {p.teams.join(', ')}
                  </div>
                )}
                {p.work_item_types && p.work_item_types.length > 0 && (
                  <div className="discovery-details-line">
                    <span className="form-hint">Work item types ({p.work_item_types.length}): </span>
                    {p.work_item_types.join(', ')}
                  </div>
                )}
                {p.iteration_count != null && p.iteration_count > 0 && (
                  <div className="discovery-details-line">
                    <span className="form-hint">Iterations: {p.iteration_count}</span>
                  </div>
                )}
              </div>
            ) : null,
          )}
        </details>
      )}

      {(projectDetails ?? []).some((p) => (p.test_plans?.length ?? 0) > 0) && (
        <details className="discovery-details-group" style={{ marginTop: 16 }}>
          <summary className="form-hint">Test plans by project</summary>
          {(projectDetails ?? []).map((p) =>
            (p.test_plans?.length ?? 0) > 0 ? (
              <div key={`tp-${p.project}`} className="discovery-details-section">
                <div className="discovery-details-title">{p.project}</div>
                <div className="discovery-details-list">
                  {p.test_plans!.map((tp) => (
                    <div key={`${p.project}-${tp.id}`} className="discovery-details-row">
                      <span className="discovery-details-name">{tp.name}</span>
                      {tp.state ? (
                        <span className="discovery-details-badge">{tp.state}</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null,
          )}
        </details>
      )}
    </div>
  );
}
