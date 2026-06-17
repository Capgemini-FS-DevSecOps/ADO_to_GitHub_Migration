'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import {
  EditIcon,
  IconButton,
  TrashIcon,
  ValidateButton,
  ValidationResult,
} from '@/components/CredentialActions';
import { PlusIcon } from '@/components/Icons';
import {
  activateMigrationProfile,
  appealProfile,
  deleteMigrationProfile,
  fetchMyPendingProfiles,
  fetchOnboardingStatus,
  fetchSettings,
  setProfileDefault,
  validateMigrationProfile,
} from '@/lib/api';
import { fetchSession } from '@/lib/auth';
import {
  canManageSettings,
  isProfilesReadOnly,
  modelsAccessDeniedMessage,
  profilesReadOnlyHint,
} from '@/lib/permissions';
import type { MigrationProfile } from '@/lib/types';

export default function MigrationProfilesPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const [validatingId, setValidatingId] = useState<string | null>(null);
  const [validation, setValidation] = useState<Record<string, { valid: boolean; message: string }>>({});
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [replacementId, setReplacementId] = useState<string>('');

  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const { data: onboarding } = useQuery({ queryKey: ['onboarding'], queryFn: fetchOnboardingStatus });
  const { data: myPending } = useQuery({ queryKey: ['my-pending'], queryFn: fetchMyPendingProfiles });
  const canManage = canManageSettings(session?.permissions);
  const readOnlyProfiles = isProfilesReadOnly(session?.permissions);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });

  const deleteMut = useMutation({
    mutationFn: ({ id, newDefaultId }: { id: string; newDefaultId?: string }) =>
      deleteMigrationProfile(id, newDefaultId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      setDeleteConfirm(null);
      setReplacementId('');
    },
  });

  const appealMut = useMutation({
    mutationFn: appealProfile,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-pending'] }),
  });

  const runValidate = async (id: string) => {
    setValidatingId(id);
    try {
      const result = await validateMigrationProfile(id);
      setValidation((prev) => ({ ...prev, [id]: result }));
    } catch (e) {
      setValidation((prev) => ({
        ...prev,
        [id]: { valid: false, message: e instanceof Error ? e.message : 'Validation failed' },
      }));
    } finally {
      setValidatingId(null);
    }
  };

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  const profiles = settings?.migration_profiles ?? [];
  const activeProfiles = profiles.filter((p) => p.status === 'active' || !p.status);
  const soleActive = activeProfiles.length <= 1;

  return (
    <div>
      <div className="settings-list-header">
        <div>
          <h2 className="oai-subsection-title" style={{ margin: 0 }}>
            Migration profiles
          </h2>
          <p className="form-hint" style={{ margin: '4px 0 0' }}>
            Each profile pairs one Azure DevOps source org with one GitHub target org and its PATs.
            {readOnlyProfiles && <> {profilesReadOnlyHint()}</>}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {canManage && (
            <Link href="/settings/profiles/pending" className="oai-button oai-button-secondary">
              Pending approval
            </Link>
          )}
          {canManage && onboarding?.can_submit_profile && (
            <Link
              href="/settings/profiles/new"
              className="oai-button oai-button-primary"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <PlusIcon size={16} color="#fff" /> Add migration profile
            </Link>
          )}
        </div>
      </div>

      {myPending && myPending.length > 0 && (
        <div className="oai-card" style={{ marginBottom: 16 }}>
          <h3 className="oai-subsection-title">Your submissions</h3>
          {myPending.map((p) => (
            <div key={p.id} style={{ marginTop: 8 }}>
              <strong>{p.name}</strong>
              <span className="step-badge" style={{ marginLeft: 8 }}>{p.status}</span>
              {p.status === 'denied' && (
                <button
                  type="button"
                  className="oai-button oai-button-secondary"
                  style={{ marginLeft: 8 }}
                  onClick={() => appealMut.mutate(p.id)}
                >
                  Appeal
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {profiles.map((p: MigrationProfile) => (
        <div key={p.id} className="oai-card credential-card">
          <div className="credential-card-main">
            <div>
              <strong>{p.name}</strong>
              {p.is_default && (
                <span className="step-badge step-badge-completed" style={{ marginLeft: 8 }}>default</span>
              )}
              {settings?.active_profile_id === p.id && (
                <span className="step-badge step-badge-completed" style={{ marginLeft: 8 }}>active session</span>
              )}
              {p.status && p.status !== 'active' && (
                <span className="step-badge" style={{ marginLeft: 8 }}>{p.status}</span>
              )}
              <p className="credential-meta">
                <span className="endpoint-label">Source</span> {p.ado_org_url}
              </p>
              <p className="credential-meta">
                <span className="endpoint-label">Target</span> {p.gh_org || '—'}{' '}
                · {p.github_tokens?.length ?? 0} token(s)
                {p.last_scan_at && <> · scanned {new Date(p.last_scan_at).toLocaleDateString()}</>}
              </p>
              {validation[p.id] && (
                <ValidationResult valid={validation[p.id].valid} message={validation[p.id].message} />
              )}
            </div>
            <div className="credential-actions">
              <ValidateButton
                onClick={() => runValidate(p.id)}
                loading={validatingId === p.id}
                label="Test migration profile"
              />
              {p.status === 'active' && settings?.active_profile_id !== p.id && (
                <button
                  type="button"
                  className="oai-button oai-button-secondary credential-action-btn"
                  onClick={() =>
                    activateMigrationProfile(p.id).then(() => qc.invalidateQueries({ queryKey: ['settings'] }))
                  }
                >
                  Activate
                </button>
              )}
              {canManage && p.status === 'active' && !p.is_default && (
                <button
                  type="button"
                  className="oai-button oai-button-secondary credential-action-btn"
                  onClick={() =>
                    setProfileDefault(p.id).then(() => qc.invalidateQueries({ queryKey: ['settings'] }))
                  }
                >
                  Set default
                </button>
              )}
              <button
                type="button"
                className="oai-button oai-button-secondary credential-action-btn"
                onClick={() => router.push(`/settings/profiles/${p.id}/source`)}
                disabled={readOnlyProfiles}
                title={readOnlyProfiles ? 'Read-only for operators' : undefined}
              >
                Configure
              </button>
              {canManage && (
                <IconButton title="Edit profile" onClick={() => router.push(`/settings/profiles/${p.id}/source`)}>
                  <EditIcon />
                </IconButton>
              )}
              {canManage && (
                <>
                  {deleteConfirm === p.id ? (
                    <>
                      {p.is_default && activeProfiles.length > 1 && (
                        <select
                          className="oai-input"
                          value={replacementId}
                          onChange={(e) => setReplacementId(e.target.value)}
                        >
                          <option value="">Pick new default…</option>
                          {activeProfiles
                            .filter((x) => x.id !== p.id)
                            .map((x) => (
                              <option key={x.id} value={x.id}>{x.name}</option>
                            ))}
                        </select>
                      )}
                      <button
                        type="button"
                        className="oai-button confirm-delete-btn credential-action-btn"
                        disabled={p.is_default && activeProfiles.length > 1 && !replacementId}
                        onClick={() =>
                          deleteMut.mutate({
                            id: p.id,
                            newDefaultId: replacementId || undefined,
                          })
                        }
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        className="oai-button oai-button-secondary credential-action-btn"
                        onClick={() => {
                          setDeleteConfirm(null);
                          setReplacementId('');
                        }}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <IconButton
                      title={soleActive && p.status === 'active' ? 'Cannot delete sole active profile' : 'Delete profile'}
                      variant="danger"
                      onClick={() => !soleActive || p.status !== 'active' ? setDeleteConfirm(p.id) : undefined}
                    >
                      <TrashIcon />
                    </IconButton>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      ))}

      {!profiles.length && (
        <div className="oai-card oai-welcome-card">
          <p>Create a migration profile for each ADO → GitHub org pair you want to migrate.</p>
          {onboarding?.can_submit_profile && (
            <Link
              href="/settings/profiles/new"
              className="oai-button oai-button-primary"
              style={{ marginTop: 12, display: 'inline-block' }}
            >
              Add migration profile
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
