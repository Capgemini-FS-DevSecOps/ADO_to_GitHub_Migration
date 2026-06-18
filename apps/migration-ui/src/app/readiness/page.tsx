'use client';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { fetchReadiness, fetchSettings } from '@/lib/api';
import type { PipelineReadinessItem } from '@/lib/types';

const PAGE_SIZE = 25;

function readinessLabel(item: PipelineReadinessItem): string {
  return item.classification || item.conversion || '—';
}

function migrationLabel(status?: string): string {
  switch (status) {
    case 'migrated':
      return 'Migrated';
    case 'in_progress':
      return 'In progress';
    case 'failed':
      return 'Failed';
    case 'not_migrated':
      return 'Not migrated';
    default:
      return status || 'Not migrated';
  }
}

function migrationClass(status?: string): string {
  switch (status) {
    case 'migrated':
      return 'badge-auto';
    case 'in_progress':
      return 'badge-assisted';
    case 'failed':
      return 'badge-manual';
    default:
      return '';
  }
}

export default function ReadinessPage() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const profileId = settings?.active_profile_id ?? '';
  const active = settings?.migration_profiles?.find((p) => p.id === profileId);

  const [scanVersion, setScanVersion] = useState(0);
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');
  const [readinessFilter, setReadinessFilter] = useState('');
  const [migrationFilter, setMigrationFilter] = useState('');

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ['readiness', scanVersion],
    queryFn: () => fetchReadiness({ refreshInventory: scanVersion > 0 }),
    retry: 1,
  });

  useEffect(() => {
    setPage(0);
  }, [search, readinessFilter, migrationFilter]);

  const pipelines = data?.pipelines ?? [];
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return pipelines.filter((p) => {
      if (readinessFilter && readinessLabel(p) !== readinessFilter) return false;
      if (migrationFilter && (p.migration_status || 'not_migrated') !== migrationFilter) {
        return false;
      }
      if (!needle) return true;
      const hay = [
        p.project,
        p.pipeline_name,
        p.repo_name,
        p.pipeline_type,
        readinessLabel(p),
        migrationLabel(p.migration_status),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [pipelines, search, readinessFilter, migrationFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  const handleRescan = () => {
    setScanVersion((v) => v + 1);
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h1 className="oai-page-title">Pipeline Readiness</h1>
          <p className="oai-muted">
            All Azure DevOps pipelines in the organization with conversion readiness and migration status.
          </p>
          {active && (
            <p className="form-hint" style={{ marginTop: 8 }}>
              ADO org: <strong>{active.ado_org_url}</strong>
              {data?.total_pipelines != null && (
                <> · <strong>{data.total_pipelines}</strong> pipelines indexed</>
              )}
            </p>
          )}
        </div>
        <button
          type="button"
          className="oai-button oai-button-secondary"
          disabled={isFetching}
          onClick={handleRescan}
        >
          {isFetching ? 'Scanning ADO…' : 'Rescan ADO organization'}
        </button>
      </div>

      {isLoading && (
        <div className="oai-loading">
          <div className="oai-spinner" />
          <p>Scanning pipelines and assessing readiness…</p>
        </div>
      )}

      {error && !isLoading && (
        <div className="oai-error">
          {error instanceof Error ? error.message : 'Readiness unavailable'}
          {!active && (
            <p style={{ marginTop: 8 }}>
              Configure an active migration profile with ADO credentials, then rescan.
            </p>
          )}
        </div>
      )}

      {data && !isLoading && (
        <>
          <div className="metrics-grid" style={{ marginTop: 16 }}>
            <div className="metric-card">
              <div className="metric-label">Auto</div>
              <div className="metric-value badge-auto">{data.auto}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Assisted</div>
              <div className="metric-value badge-assisted">{data.assisted}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Manual</div>
              <div className="metric-value badge-manual">{data.manual}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Est. effort</div>
              <div className="metric-value">{data.total_effort_hours?.toFixed?.(0) ?? 0}h</div>
            </div>
          </div>

          <div className="oai-card" style={{ marginTop: 16, marginBottom: 16 }}>
            <div className="history-filters">
              <label className="history-filter-field history-filter-search">
                <span>Search</span>
                <input
                  className="oai-input"
                  type="search"
                  placeholder="Pipeline, project, repo…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <label className="history-filter-field">
                <span>Readiness</span>
                <select
                  className="oai-input"
                  value={readinessFilter}
                  onChange={(e) => setReadinessFilter(e.target.value)}
                >
                  <option value="">All</option>
                  <option value="auto">Auto</option>
                  <option value="assisted">Assisted</option>
                  <option value="manual">Manual</option>
                </select>
              </label>
              <label className="history-filter-field">
                <span>Migration</span>
                <select
                  className="oai-input"
                  value={migrationFilter}
                  onChange={(e) => setMigrationFilter(e.target.value)}
                >
                  <option value="">All</option>
                  <option value="migrated">Migrated</option>
                  <option value="not_migrated">Not migrated</option>
                  <option value="in_progress">In progress</option>
                  <option value="failed">Failed</option>
                </select>
              </label>
            </div>
          </div>

          {pipelines.length === 0 ? (
            <div className="oai-card oai-welcome-card">
              <p>
                No pipelines found in inventory. Click <strong>Rescan ADO organization</strong> to
                scan all projects and build the pipeline catalog.
              </p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="oai-card oai-welcome-card">
              <p>No pipelines match the current filters.</p>
            </div>
          ) : (
            <div className="oai-card table-responsive">
              <h2 className="oai-subsection-title">All pipelines</h2>
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Pipeline</th>
                    <th>Repo</th>
                    <th>Type</th>
                    <th>Readiness</th>
                    <th>Migration</th>
                    <th>Hours</th>
                    <th>SC</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((p) => {
                    const notes = [
                      ...(p.blockers ?? []).map((b) => `Blocker: ${b}`),
                      ...(p.warnings ?? []).slice(0, 2),
                    ].join(' · ');
                    return (
                      <tr key={`${p.project}-${p.pipeline_id}`}>
                        <td>{p.project}</td>
                        <td>{p.pipeline_name}</td>
                        <td className="history-cell-muted">{p.repo_name || '—'}</td>
                        <td className="history-cell-muted">{p.pipeline_type || '—'}</td>
                        <td>
                          <span className={`badge-${readinessLabel(p)}`}>
                            {readinessLabel(p)}
                          </span>
                        </td>
                        <td>
                          <span className={migrationClass(p.migration_status)}>
                            {migrationLabel(p.migration_status)}
                          </span>
                          {p.workflow_file ? (
                            <div className="history-cell-muted" style={{ fontSize: 11, marginTop: 4 }}>
                              {p.workflow_file}
                            </div>
                          ) : null}
                        </td>
                        <td>{p.effort_hours}</td>
                        <td>{p.service_connections}</td>
                        <td className="history-cell-muted" style={{ maxWidth: 280 }}>
                          {notes || '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {filtered.length > PAGE_SIZE && (
            <div className="history-pagination">
              <span className="form-hint">
                {filtered.length} pipeline{filtered.length === 1 ? '' : 's'}
                {' · '}
                Page {page + 1} of {totalPages}
              </span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  type="button"
                  className="oai-button oai-button-secondary"
                  disabled={page <= 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="oai-button oai-button-secondary"
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
