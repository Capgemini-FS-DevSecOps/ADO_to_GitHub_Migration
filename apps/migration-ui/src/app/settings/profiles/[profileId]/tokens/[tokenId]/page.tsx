'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { ValidateButton, ValidationResult } from '@/components/CredentialActions';
import {
  fetchMigrationProfile,
  saveGitHubToken,
  validateGitHubTokenInline,
  validateGitHubTokenSaved,
} from '@/lib/api';

export default function EditProfileTokenPage({
  params,
}: {
  params: { profileId: string; tokenId: string };
}) {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: profile, isLoading } = useQuery({
    queryKey: ['migration-profile', params.profileId],
    queryFn: () => fetchMigrationProfile(params.profileId),
  });
  const token = profile?.github_tokens.find((t) => t.id === params.tokenId);

  const [form, setForm] = useState({ name: '', token: '', note: '' });
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<{
    valid: boolean;
    message: string;
    scopes?: string[];
    warnings?: string[];
  } | null>(null);

  useEffect(() => {
    if (token) {
      setForm({ name: token.name, token: '', note: token.note ?? '' });
    }
  }, [token]);

  const saveMut = useMutation({
    mutationFn: () => saveGitHubToken(params.profileId, form, params.tokenId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
      router.push(`/settings/profiles/${params.profileId}/tokens`);
    },
  });

  const testConnection = async () => {
    setValidating(true);
    try {
      const result = form.token
        ? await validateGitHubTokenInline(params.profileId, form.token)
        : await validateGitHubTokenSaved(params.profileId, params.tokenId);
      setValidation(result);
    } catch (e) {
      setValidation({ valid: false, message: e instanceof Error ? e.message : 'Validation failed' });
    } finally {
      setValidating(false);
    }
  };

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  if (!token) {
    return (
      <div className="oai-error">
        Token not found.{' '}
        <Link href={`/settings/profiles/${params.profileId}/tokens`}>Back to tokens</Link>
      </div>
    );
  }

  return (
    <div className="oai-card form-page">
      <div className="form-page-header">
        <h2 className="oai-subsection-title">Edit GitHub token</h2>
        <Link
          href={`/settings/profiles/${params.profileId}/tokens`}
          className="oai-button oai-button-secondary"
        >
          Cancel
        </Link>
      </div>
      <p className="form-hint">
        Validates against target org <strong>{profile?.gh_org || '—'}</strong>.
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
            placeholder="Leave blank to keep current token"
            onChange={(e) => setForm({ ...form, token: e.target.value })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="note">Note</label>
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
          disabled={!form.name || saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          Update token
        </button>
      </div>
    </div>
  );
}
