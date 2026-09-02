'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { DiscoveryTable } from '@/components/DiscoveryTable';
import { DiscoveryOrgInventory } from '@/components/DiscoveryOrgInventory';
import { ScanCompleteBanner } from '@/components/ScanCompleteBanner';
import { ScanDiagnostics } from '@/components/ScanDiagnostics';
import { UnifiedSubNavigation } from '@/components/UnifiedNavigation';
import { DISCOVERY_SUB_TABS } from '@/lib/navigationState';
import { WorkflowsView, ValidationView } from './WorkflowsAndValidation';
import {
  fetchDiscovery,
  fetchProfileScanStatus,
  fetchReadiness,
  fetchSettings,
  startProfileScan,
} from '@/lib/api';
import type { PipelineReadinessItem } from '@/lib/types';

const PAGE_SIZE = 25;

function formatScanBanner(status: {
  repos_scanned?: number | null;
  projects_scanned?: number | null;
  service_connections?: number | null;
  scanned_at?: string | null;
}): string {
  const repos = status.repos_scanned ?? 0;
  const projects = status.projects_scanned ?? 0;
  const sc = status.service_connections ?? 0;
  const when = status.scanned_at
    ? new Date(status.scanned_at).toLocaleString()
    : 'just now';
  return `ADO scan complete (${when}) — ${repos} repos across ${projects} projects, ${sc} service connections indexed.`;
}

function readinessLabel(item: PipelineReadinessItem): string {
  return item.classification || item.conversion || '—';
}

function migrationLabel(status?: string): string {
  switch (status) {
    case 'migrated': return 'Migrated';
    case 'in_progress': return 'In progress';
    case 'failed': return 'Failed';
    case 'repo_migrated': return 'Repo migrated';
    case 'not_migrated': return 'Not migrated';
    default: return status || 'Not migrated';
  }
}

function migrationClass(status?: string): string {
  switch (status) {
    case 'migrated': return 'badge-auto';
    case 'repo_migrated': return 'badge-assisted';
    case 'in_progress': return 'badge-assisted';
    case 'failed': return 'badge-manual';
    default: return '';
  }
}

export default function DiscoveryPage() {
  const searchParams = useSearchParams();
  const tab = searchParams.get('tab');
  const subTab = ['readiness', 'workflows', 'validation'].includes(tab ?? '')
    ? (tab as 'readiness' | 'workflows' | 'validation')
    : 'overview';

  if (subTab === 'readiness') {
    return <ReadinessView />;
  }
  if (subTab === 'workflows') {
    return <WorkflowsView />;
  }
  if (subTab === 'validation') {
    return <ValidationView />;
  }
  return <OverviewView />;
}

function OverviewView() {
  const qc = useQueryClient();
  const [bannerMessage, setBannerMessage] = useState<string | null>(null);
  const prevRunning = useRef(false);
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const profileId = settings?.active_profile_id;

  const {
    data: discovery,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['discovery', profileId],
    queryFn: () => fetchDiscovery(profileId!),
    enabled: !!profileId,
    retry: false,
  });

  const { data: scanStatus } = useQuery({
    queryKey: ['scan-status', profileId],
    queryFn: () => fetchProfileScanStatus(profileId!),
    enabled: !!profileId,
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
  });

  const scanMut = useMutation({
    mutationFn: () => startProfileScan(profileId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scan-status', profileId] });
    },
  });

  useEffect(() => {
    const running = scanStatus?.running ?? false;
    if (prevRunning.current && !running) {
      if (scanStatus?.status === 'completed') {
        setBannerMessage(formatScanBanner(scanStatus));
        qc.invalidateQueries({ queryKey: ['discovery', profileId] });
        qc.invalidateQueries({ queryKey: ['profile-scan', profileId] });
        qc.invalidateQueries({ queryKey: ['settings'] });
      } else if (scanStatus?.status === 'failed' && scanStatus.error) {
        setBannerMessage(`ADO scan failed: ${scanStatus.error}`);
      }
    }
    prevRunning.current = running;
  }, [scanStatus, profileId, qc]);

  const active = settings?.migration_profiles?.find((p) => p.id === profileId);
  const scanning = scanMut.isPending || (scanStatus?.running ?? false);
  const projectsScanned = discovery?.projects_scanned ?? active?.scan_summary?.projects_scanned ?? 0;
  const reposScanned = discovery?.repos_scanned ?? active?.scan_summary?.repos_scanned ?? 0;

  return (
    <div>
      <UnifiedSubNavigation tabs={DISCOVERY_SUB_TABS} />

      <h1 className="oai-page-title">Discovery Overview</h1>
      <p>
        Full ADO scan: repositories, pipelines, service connections, variable groups,
        environments, artifacts, Azure Boards (work items, teams, iterations), and test plans.
        Review risk scores and pipeline inventory.
      </p>

      {bannerMessage && (
        <ScanCompleteBanner
          message={bannerMessage}
          onDismiss={() => setBannerMessage(null)}
        />
      )}

      {!profileId && (
        <div className="oai-card oai-welcome-card">
          <p>
            No active migration profile —{' '}
            <a href="/settings/profiles">create or activate one</a>, then run a scan.
          </p>
        </div>
      )}

      {profileId && active && (
        <div className="oai-card discovery-profile-bar">
          <div>
            <strong>{active.name}</strong>
            <span className="discovery-profile-meta">
              {active.ado_org_url} → {active.gh_org || '(GitHub org not set)'}
            </span>
          </div>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            disabled={scanning}
            onClick={() => scanMut.mutate()}
          >
            {scanning ? 'Scanning ADO org in background…' : 'Re-scan ADO org'}
          </button>
          {scanMut.isError && (
            <p className="badge-manual">{String(scanMut.error)}</p>
          )}
          {scanning && (
            <p className="scan-status-banner">
              Scan running in the background — you can keep using the console. Results will refresh
              when complete.
            </p>
          )}
        </div>
      )}

      {profileId && isLoading && <p className="oai-loading">Loading discovery data…</p>}

      {profileId && isError && (
        <div className="oai-card oai-welcome-card">
          <p>Could not load discovery — try re-scanning the ADO org.</p>
          {error && <p className="badge-manual">{String(error)}</p>}
        </div>
      )}

      {profileId && discovery && (
        <div className="oai-card" style={{ marginBottom: '1rem' }}>
          <DiscoveryOrgInventory
            orgInventory={discovery.org_inventory}
            projectDetails={discovery.project_details}
            pipelineInventoryCount={discovery.pipeline_inventory_count}
          />
        </div>
      )}

      {profileId && discovery && (
        <div className="oai-card" style={{ marginBottom: '1rem' }}>
          <h2 className="oai-subsection-title">Scan diagnostics</h2>
          <ScanDiagnostics
            projectsScanned={projectsScanned}
            reposScanned={reposScanned}
            projectDetails={discovery.project_details}
            warnings={discovery.warnings}
            status={discovery.status}
          />
        </div>
      )}

      {profileId && discovery && reposScanned === 0 && !scanning && (
        <div className="oai-card oai-welcome-card">
          <p>
            The scan reached Azure DevOps successfully but returned <strong>0 Git repositories</strong>.
            Check the project breakdown above — if repo counts are 0 everywhere, verify the PAT has{' '}
            <strong>Code (read)</strong> scope and that these projects actually contain Git repos
            (not TFVC-only or empty).
          </p>
        </div>
      )}

      {discovery && discovery.repos.length > 0 && (
        <div className="oai-card">
          <h2 className="oai-subsection-title">Repository inventory</h2>
          <DiscoveryTable repos={discovery.repos} />
        </div>
      )}

      {discovery && discovery.inventory_gaps && discovery.inventory_gaps.length > 0 && (
        <div className="oai-card">
          <h2 className="oai-subsection-title">Inventory gaps</h2>
          <p className="form-hint">
            ADO assets that need operator input before secrets or pipeline conversion.
          </p>
          <div className="discovery-table-scroll">
            <table className="discovery-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Project</th>
                  <th>Name</th>
                  <th>Hint</th>
                </tr>
              </thead>
              <tbody>
                {discovery.inventory_gaps.map((gap, i) => (
                  <tr key={`gap-${i}`}>
                    <td>{gap.type ?? '—'}</td>
                    <td>{gap.project ?? '—'}</td>
                    <td>{gap.name ?? '—'}</td>
                    <td className="history-cell-muted">{gap.hint ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function ReadinessView() {
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
      <UnifiedSubNavigation tabs={DISCOVERY_SUB_TABS} />

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
