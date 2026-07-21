'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  EditIcon,
  IconButton,
  TrashIcon,
  ValidateButton,
  ValidationResult,
} from '@/components/CredentialActions';
import { ScanRecommendations } from '@/components/ScanRecommendations';
import { SearchIcon } from '@/components/Icons';
import {
  deleteGitHubToken,
  fetchMigrationProfile,
  fetchProfileScan,
  saveMigrationProfile,
  scanMigrationProfile,
  validateGitHubTokenSaved,
} from '@/lib/api';
import type { GitHubTokenEntry } from '@/lib/types';

export default function ProfileTokensPage({ params }: { params: { profileId: string } }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [validatingId, setValidatingId] = useState<string | null>(null);
  const [sessionValidation, setSessionValidation] = useState<
    Record<string, { valid: boolean; message: string; scopes?: string[]; warnings?: string[] }>
  >({});
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [ghOrg, setGhOrg] = useState('');
  const [scanning, setScanning] = useState(false);

  const { data: profile, isLoading } = useQuery({
    queryKey: ['migration-profile', params.profileId],
    queryFn: () => fetchMigrationProfile(params.profileId),
  });

  const { data: scanResult, refetch: refetchScan } = useQuery({
    queryKey: ['profile-scan', params.profileId],
    queryFn: () => fetchProfileScan(params.profileId),
    enabled: !!profile?.last_scan_at,
    retry: false,
  });

  useEffect(() => {
    if (profile) setGhOrg(profile.gh_org);
  }, [profile]);

  const saveOrgMut = useMutation({
    mutationFn: () =>
      saveMigrationProfile({ name: profile!.name, gh_org: ghOrg, ado_org_url: profile!.ado_org_url }, params.profileId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: (tokenId: string) => deleteGitHubToken(params.profileId, tokenId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
      qc.invalidateQueries({ queryKey: ['settings'] });
      setDeleteConfirm(null);
    },
  });

  const runValidate = async (token: GitHubTokenEntry) => {
    setValidatingId(token.id);
    try {
      const result = await validateGitHubTokenSaved(params.profileId, token.id);
      setSessionValidation((prev) => ({ ...prev, [token.id]: result }));
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
    } catch (e) {
      setSessionValidation((prev) => ({
        ...prev,
        [token.id]: {
          valid: false,
          message: e instanceof Error ? e.message : 'Validation failed',
        },
      }));
    } finally {
      setValidatingId(null);
    }
  };

  const runScan = async () => {
    setScanning(true);
    try {
      await scanMigrationProfile(params.profileId);
      await refetchScan();
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
    } finally {
      setScanning(false);
    }
  };

  if (isLoading || !profile) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  const tokens = profile.github_tokens ?? [];

  return (
    <div>
      <div className="oai-card" style={{ marginBottom: '1rem' }}>
        <h3 className="wizard-slide-title" style={{ marginBottom: 12 }}>
          Target GitHub organization
        </h3>
        <div className="form-row" style={{ maxWidth: 400 }}>
          <label htmlFor="gh_org">GitHub org</label>
          <input
            id="gh_org"
            className="oai-input"
            value={ghOrg}
            onChange={(e) => setGhOrg(e.target.value)}
          />
        </div>
        <div className="form-actions" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            disabled={saveOrgMut.isPending || ghOrg === profile.gh_org}
            onClick={() => saveOrgMut.mutate()}
          >
            Save org
          </button>
        </div>
      </div>

      <div className="settings-list-header">
        <div>
          <h2 className="oai-subsection-title" style={{ margin: 0 }}>
            GitHub tokens
          </h2>
          <p className="form-hint" style={{ margin: '4px 0 0' }}>
            PAT validation uses target org <strong>{profile.gh_org || '—'}</strong>.
          </p>
        </div>
        <Link
          href={`/settings/profiles/${params.profileId}/tokens/new`}
          className="oai-button oai-button-primary"
        >
          Add token
        </Link>
      </div>

      {tokens.map((t, index) => {
        const live = sessionValidation[t.id] ?? t.last_validation;
        return (
          <div key={t.id} className="oai-card credential-card">
            <div className="credential-card-main">
              <div>
                <strong>{t.name}</strong>
                <span className="token-slot-badge">GH_TOKEN{index === 0 ? '' : `_${index + 1}`}</span>
                {t.note && <p className="credential-meta">{t.note}</p>}
                {t.last_validated_at && (
                  <p className="credential-meta">
                    Last tested: {new Date(t.last_validated_at).toLocaleString()}
                  </p>
                )}
                {live && (
                  <ValidationResult
                    valid={live.valid}
                    message={live.message}
                    scopes={live.scopes}
                    warnings={live.warnings}
                  />
                )}
              </div>
              <div className="credential-actions">
                <ValidateButton
                  onClick={() => runValidate(t)}
                  loading={validatingId === t.id}
                  label="Test connection"
                />
                <IconButton
                  title="Edit token"
                  onClick={() =>
                    router.push(`/settings/profiles/${params.profileId}/tokens/${t.id}`)
                  }
                >
                  <EditIcon />
                </IconButton>
                {deleteConfirm === t.id ? (
                  <>
                    <button
                      type="button"
                      className="oai-button confirm-delete-btn credential-action-btn"
                      onClick={() => deleteMut.mutate(t.id)}
                    >
                      Confirm
                    </button>
                    <button
                      type="button"
                      className="oai-button oai-button-secondary credential-action-btn"
                      onClick={() => setDeleteConfirm(null)}
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <IconButton title="Delete token" variant="danger" onClick={() => setDeleteConfirm(t.id)}>
                    <TrashIcon />
                  </IconButton>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {!tokens.length && (
        <div className="oai-card oai-welcome-card">
          <p>Add GitHub PATs for target org {profile.gh_org || '—'}.</p>
          <Link
            href={`/settings/profiles/${params.profileId}/tokens/new`}
            className="oai-button oai-button-primary"
            style={{ marginTop: 12, display: 'inline-block' }}
          >
            Add token
          </Link>
        </div>
      )}

      <div className="oai-card" style={{ marginTop: '1.5rem' }}>
        <div className="section-header">
          <span className="section-icon-svg"><SearchIcon size={22} color="#35b8ff" /></span>
          <h2 className="section-title">Migration scan</h2>
        </div>
        <p className="form-hint">
          Scan ADO repos and get phase recommendations (POC → Pilot → Wave 1–3) based on risk scoring.
        </p>
        <button
          type="button"
          className="oai-button oai-button-primary"
          onClick={runScan}
          disabled={scanning}
          style={{ marginBottom: 16 }}
        >
          {scanning ? 'Scanning…' : profile.last_scan_at ? 'Re-scan repositories' : 'Scan repositories'}
        </button>
        {scanResult && <ScanRecommendations scan={scanResult} />}
        {!scanResult && profile.scan_summary?.recommendations && (
          <div className="scan-phase-grid scan-phase-grid-compact">
            {Object.entries(profile.scan_summary.recommendations).map(([phase, bucket]) => (
              <div key={phase} className="scan-phase-card">
                <div className="scan-phase-header">
                  <span className="scan-phase-name">{phase.toUpperCase()}</span>
                  <span className="scan-phase-count">{bucket.repo_count} repos</span>
                </div>
                <p className="scan-phase-rationale">{bucket.rationale}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
