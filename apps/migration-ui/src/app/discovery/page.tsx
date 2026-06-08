'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { DiscoveryTable } from '@/components/DiscoveryTable';
import { ScanDiagnostics } from '@/components/ScanDiagnostics';
import { ScanRecommendations } from '@/components/ScanRecommendations';
import {
  fetchDiscovery,
  fetchPhases,
  fetchSettings,
  savePhaseAssignments,
  scanMigrationProfile,
} from '@/lib/api';
import type { MigrationScanResult } from '@/lib/types';

export default function DiscoveryPage() {
  const qc = useQueryClient();
  const [lastScan, setLastScan] = useState<MigrationScanResult | null>(null);
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

  const scanMut = useMutation({
    mutationFn: () => scanMigrationProfile(profileId!),
    onSuccess: (result) => {
      setLastScan(result);
      qc.invalidateQueries({ queryKey: ['discovery', profileId] });
      qc.invalidateQueries({ queryKey: ['profile-scan', profileId] });
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const saveMut = useMutation({
    mutationFn: (assignments: { project: string; repo_name: string; assigned_phase: string }[]) =>
      savePhaseAssignments(profileId!, assignments),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['discovery', profileId] });
      qc.invalidateQueries({ queryKey: ['profile-scan', profileId] });
    },
  });

  const active = settings?.migration_profiles?.find((p) => p.id === profileId);
  const display = lastScan ?? discovery;
  const projectsScanned = display?.projects_scanned ?? active?.scan_summary?.projects_scanned ?? 0;
  const reposScanned = display?.repos_scanned ?? active?.scan_summary?.repos_scanned ?? 0;

  return (
    <div>
      <h1 className="oai-page-title">Discovery</h1>
      <p>
        Repos discovered during the profile scan. Review risk scores, suggested phases, and assign
        final migration phases before running Migrate.
      </p>

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
            disabled={scanMut.isPending}
            onClick={() => scanMut.mutate()}
          >
            {scanMut.isPending ? 'Scanning…' : 'Re-scan ADO org'}
          </button>
          {scanMut.isError && (
            <p className="badge-manual">{String(scanMut.error)}</p>
          )}
          {scanMut.isSuccess && scanMut.data?.status === 'empty' && (
            <p className="scan-status-banner">Scan completed — no Git repositories found (see details below).</p>
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

      {profileId && display && (
        <div className="oai-card" style={{ marginBottom: '1rem' }}>
          <ScanDiagnostics
            projectsScanned={projectsScanned}
            reposScanned={reposScanned}
            projectDetails={display.project_details}
            warnings={display.warnings}
            status={display.status}
          />
        </div>
      )}

      {profileId && display && reposScanned === 0 && !scanMut.isPending && (
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
            <h2 className="oai-subsection-title">Repository inventory</h2>
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
