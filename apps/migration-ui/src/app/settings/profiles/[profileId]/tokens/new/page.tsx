'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { ValidateButton, ValidationResult } from '@/components/CredentialActions';
import {
  fetchMigrationProfile,
  saveGitHubToken,
  validateGitHubTokenInline,
} from '@/lib/api';

export default function NewProfileTokenPage({ params }: { params: { profileId: string } }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: profile } = useQuery({
    queryKey: ['migration-profile', params.profileId],
    queryFn: () => fetchMigrationProfile(params.profileId),
  });

  const [form, setForm] = useState({ name: '', token: '', note: '' });
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<{
    valid: boolean;
    message: string;
    scopes?: string[];
    warnings?: string[];
  } | null>(null);

  const saveMut = useMutation({
    mutationFn: () => saveGitHubToken(params.profileId, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
      qc.invalidateQueries({ queryKey: ['settings'] });
      router.push(`/settings/profiles/${params.profileId}/tokens`);
    },
  });

  const testConnection = async () => {
    setValidating(true);
    try {
      const result = await validateGitHubTokenInline(params.profileId, form.token);
      setValidation(result);
    } catch (e) {
      setValidation({ valid: false, message: e instanceof Error ? e.message : 'Validation failed' });
    } finally {
      setValidating(false);
    }
  };

  return (
    <div className="oai-card form-page">
      <div className="form-page-header">
        <h2 className="oai-subsection-title">Add GitHub token</h2>
        <Link
          href={`/settings/profiles/${params.profileId}/tokens`}
          className="oai-button oai-button-secondary"
        >
          Cancel
        </Link>
      </div>
      <p className="form-hint">
        Validates against target org <strong>{profile?.gh_org || '—'}</strong> on this migration
        profile.
      </p>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="name">Name</label>
          <input
            id="name"
            className="oai-input"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="token">Token</label>
          <input
            id="token"
            type="password"
            className="oai-input"
            value={form.token}
            onChange={(e) => setForm({ ...form, token: e.target.value })}
            placeholder="ghp_xxxxxxxxxxxx"
          />
        </div>
        <div className="form-row">
          <label htmlFor="note">Note (optional)</label>
          <input
            id="note"
            className="oai-input"
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </div>
      </div>
      {validation && (
        <ValidationResult
          valid={validation.valid}
          message={validation.message}
          scopes={validation.scopes}
          warnings={validation.warnings}
        />
      )}
      <div className="form-actions">
        <ValidateButton onClick={testConnection} loading={validating} />
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={!form.name || !form.token || saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          Save token
        </button>
      </div>
    </div>
  );
}
