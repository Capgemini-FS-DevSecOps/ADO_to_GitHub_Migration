'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { ValidateButton, ValidationResult } from '@/components/CredentialActions';
import {
  fetchMigrationProfile,
  saveMigrationProfile,
  validateAdoForProfile,
  validateProfileSource,
} from '@/lib/api';

export default function ProfileSourcePage({ params }: { params: { profileId: string } }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: profile, isLoading } = useQuery({
    queryKey: ['migration-profile', params.profileId],
    queryFn: () => fetchMigrationProfile(params.profileId),
  });

  const [form, setForm] = useState({
    name: '',
    ado_org_url: '',
    ado_pat: '',
  });
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<{ valid: boolean; message: string } | null>(null);

  useEffect(() => {
    if (profile) {
      setForm({
        name: profile.name,
        ado_org_url: profile.ado_org_url,
        ado_pat: '',
      });
    }
  }, [profile]);

  const saveMut = useMutation({
    mutationFn: () => saveMigrationProfile(form, params.profileId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['migration-profile', params.profileId] });
    },
  });

  const testConnection = async () => {
    setValidating(true);
    try {
      const result = form.ado_pat
        ? await validateAdoForProfile(params.profileId, form.ado_org_url, form.ado_pat)
        : await validateProfileSource(params.profileId);
      setValidation(result);
    } catch (e) {
      setValidation({ valid: false, message: e instanceof Error ? e.message : 'Validation failed' });
    } finally {
      setValidating(false);
    }
  };

  if (isLoading || !profile) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  return (
    <div className="oai-card form-page">
      <div className="form-page-header">
        <h2 className="oai-subsection-title">Source connection (Azure DevOps)</h2>
        <Link
          href={`/settings/profiles/${params.profileId}/tokens`}
          className="oai-button oai-button-secondary"
        >
          Target tokens →
        </Link>
      </div>
      <p className="form-hint">
        Source credentials for Azure DevOps. Target GitHub org and PATs are configured on the
        tokens tab.
      </p>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="name">Profile name</label>
          <input
            id="name"
            className="oai-input"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="ado_org_url">ADO org URL</label>
          <input
            id="ado_org_url"
            className="oai-input"
            value={form.ado_org_url}
            onChange={(e) => setForm({ ...form, ado_org_url: e.target.value })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="ado_pat">ADO PAT</label>
          <input
            id="ado_pat"
            type="password"
            className="oai-input"
            value={form.ado_pat}
            placeholder="Leave blank to keep current PAT"
            onChange={(e) => setForm({ ...form, ado_pat: e.target.value })}
          />
        </div>
      </div>
      {validation && <ValidationResult valid={validation.valid} message={validation.message} />}
      <div className="form-actions">
        <ValidateButton onClick={testConnection} loading={validating} label="Test ADO connection" />
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={!form.name || saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          Save source
        </button>
        <button
          type="button"
          className="oai-button oai-button-secondary"
          onClick={() => router.push(`/settings/profiles/${params.profileId}/tokens`)}
        >
          Manage GitHub tokens
        </button>
      </div>
    </div>
  );
}
