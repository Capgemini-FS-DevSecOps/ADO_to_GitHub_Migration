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
  deleteMigrationProfile,
  fetchSettings,
  validateMigrationProfile,
} from '@/lib/api';
import type { MigrationProfile } from '@/lib/types';

export default function MigrationProfilesPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const [validatingId, setValidatingId] = useState<string | null>(null);
  const [validation, setValidation] = useState<Record<string, { valid: boolean; message: string }>>({});
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });

  const deleteMut = useMutation({
    mutationFn: deleteMigrationProfile,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      setDeleteConfirm(null);
    },
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

  return (
    <div>
      <div className="settings-list-header">
        <div>
          <h2 className="oai-subsection-title" style={{ margin: 0 }}>
            Migration profiles
          </h2>
          <p className="form-hint" style={{ margin: '4px 0 0' }}>
            Each profile pairs one Azure DevOps source org with one GitHub target org and its PATs.
          </p>
        </div>
        <Link href="/settings/profiles/new" className="oai-button oai-button-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <PlusIcon size={16} color="#fff" /> Add migration profile
        </Link>
      </div>

      {profiles.map((p: MigrationProfile) => (
        <div key={p.id} className="oai-card credential-card">
          <div className="credential-card-main">
            <div>
              <strong>{p.name}</strong>
              {settings?.active_profile_id === p.id && (
                <span className="step-badge step-badge-completed" style={{ marginLeft: 8 }}>
                  active
                </span>
              )}
              <p className="credential-meta">
                <span className="endpoint-label">Source</span> {p.ado_org_url}
              </p>
              <p className="credential-meta">
                <span className="endpoint-label">Target</span> {p.gh_org || '—'}{' '}
                · {p.github_tokens?.length ?? 0} token(s)
                {p.last_scan_at && (
                  <> · scanned {new Date(p.last_scan_at).toLocaleDateString()}</>
                )}
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
              <button
                type="button"
                className="oai-button oai-button-secondary credential-action-btn"
                onClick={() =>
                  activateMigrationProfile(p.id).then(() => qc.invalidateQueries({ queryKey: ['settings'] }))
                }
              >
                Activate
              </button>
              <button
                type="button"
                className="oai-button oai-button-secondary credential-action-btn"
                onClick={() => router.push(`/settings/profiles/${p.id}/source`)}
              >
                Configure
              </button>
              <IconButton
                title="Edit profile"
                onClick={() => router.push(`/settings/profiles/${p.id}/source`)}
              >
                <EditIcon />
              </IconButton>
              {deleteConfirm === p.id ? (
                <>
                  <button
                    type="button"
                    className="oai-button confirm-delete-btn credential-action-btn"
                    onClick={() => deleteMut.mutate(p.id)}
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
                <IconButton title="Delete profile" variant="danger" onClick={() => setDeleteConfirm(p.id)}>
                  <TrashIcon />
                </IconButton>
              )}
            </div>
          </div>
        </div>
      ))}

      {!profiles.length && (
        <div className="oai-card oai-welcome-card">
          <p>
            Create a migration profile for each ADO → GitHub org pair you want to migrate.
          </p>
          <Link
            href="/settings/profiles/new"
            className="oai-button oai-button-primary"
            style={{ marginTop: 12, display: 'inline-block' }}
          >
            Add migration profile
          </Link>
        </div>
      )}
    </div>
  );
}
