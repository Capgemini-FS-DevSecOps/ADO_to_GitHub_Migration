'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { DiscoveryTable } from '@/components/DiscoveryTable';
import { DiscoveryOrgInventory } from '@/components/DiscoveryOrgInventory';
import { ScanCompleteBanner } from '@/components/ScanCompleteBanner';
import { ScanDiagnostics } from '@/components/ScanDiagnostics';
import { ScanRecommendations } from '@/components/ScanRecommendations';
import {
  fetchDiscovery,
  fetchPhases,
  fetchProfileScanStatus,
  fetchSettings,
  savePhaseAssignments,
  startProfileScan,
} from '@/lib/api';

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

export default function DiscoveryPage() {
  const qc = useQueryClient();
  const [bannerMessage, setBannerMessage] = useState<string | null>(null);
  const prevRunning = useRef(false);
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const profileId = settings?.active_profile_id;

  const { data: phasesData } = useQuery({
    queryKey: ['phases', profileId],
    queryFn: () => fetchPhases(profileId!),
    enabled: !!profileId,
  });

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

  const saveMut = useMutation({
    mutationFn: (assignments: { project: string; repo_name: string; assigned_phase: string }[]) =>
      savePhaseAssignments(profileId!, assignments),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['discovery', profileId] });
      qc.invalidateQueries({ queryKey: ['profile-scan', profileId] });
      if (data.message) {
        setBannerMessage(data.message);
      }
    },
  });

  const active = settings?.migration_profiles?.find((p) => p.id === profileId);
  const scanning = scanMut.isPending || (scanStatus?.running ?? false);
  const projectsScanned = discovery?.projects_scanned ?? active?.scan_summary?.projects_scanned ?? 0;
  const reposScanned = discovery?.repos_scanned ?? active?.scan_summary?.repos_scanned ?? 0;

  return (
    <div>
      <h1 className="oai-page-title">Discovery</h1>
      <p>
        Full ADO scan: repositories, pipelines, service connections, variable groups, and
        environments. Review risk scores, pipeline inventory, and assign migration phases before
        running Migrate.
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
            <a href="/settings/profiles">create or activate one</a>, then run a scan from Settings.
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
        <>
          <div className="oai-card" style={{ marginBottom: '1rem' }}>
            <ScanRecommendations
              scan={{
                scanned_at: discovery.scanned_at,
                projects_scanned: discovery.projects_scanned ?? projectsScanned,
                repos_scanned: discovery.repos_scanned,
                total_repos: discovery.repos_scanned,
                gh_org: discovery.gh_org,
                recommendations: discovery.recommendations as never,
              }}
              phases={phasesData?.phases}
              compact
            />
          </div>

          <div className="oai-card">
            <h2 className="oai-subsection-title">Repository inventory &amp; wave assignment</h2>
            <DiscoveryTable
              repos={discovery.repos}
              phases={
                phasesData?.phases?.length
                  ? phasesData.phases
                  : (settings?.advanced?.phases?.length
                    ? settings.advanced.phases
                    : [
                        { id: 'poc', name: 'POC', risk_max: 25, repo_cap: 10, order: 0 },
                        { id: 'pilot', name: 'Pilot', risk_max: 45, repo_cap: 100, order: 1 },
                        { id: 'wave1', name: 'Wave 1', risk_max: 65, repo_cap: 500, order: 2 },
                        { id: 'wave2', name: 'Wave 2', risk_max: 80, repo_cap: 1000, order: 3 },
                        { id: 'wave3', name: 'Wave 3', risk_max: 100, repo_cap: 9999, order: 4 },
                      ])
              }
              saving={saveMut.isPending}
              onSave={(assignments) => saveMut.mutate(assignments)}
            />
            {saveMut.isSuccess && (
              <p className="discovery-save-ok">Phase assignments saved.</p>
            )}
            {saveMut.isError && (
              <p className="badge-manual">{String(saveMut.error)}</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
