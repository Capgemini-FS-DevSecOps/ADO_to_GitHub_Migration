'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { ValidationResult } from '@/components/CredentialActions';
import { CloudIcon, GithubIcon, SearchIcon } from '@/components/Icons';
import {
  setupMigrationProfile,
  validateAdoInline,
  validateGitHubInline,
} from '@/lib/api';

const STEPS = [
  { id: 'source', label: 'Source (ADO)', icon: CloudIcon },
  { id: 'target', label: 'Target (GitHub)', icon: GithubIcon },
  { id: 'confirm', label: 'Confirm', icon: SearchIcon },
];

type ProfileWizardMode = 'onboarding' | 'settings-admin' | 'settings-operator';

export function ProfileWizard({ mode = 'settings-admin' }: { mode?: ProfileWizardMode }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({
    name: '',
    ado_org_url: 'https://dev.azure.com/YOUR_ORG',
    ado_pat: '',
    gh_org: '',
    github_token: '',
    github_token_name: 'Primary',
  });
  const [adoValidation, setAdoValidation] = useState<{ valid: boolean; message: string } | null>(null);
  const [ghValidation, setGhValidation] = useState<{ valid: boolean; message: string } | null>(null);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setupMut = useMutation({
    mutationFn: () => setupMigrationProfile(form),
    onSuccess: async (p) => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      if (mode === 'onboarding' || p.status === 'active') {
        router.push(mode === 'onboarding' ? '/' : `/settings/profiles/${p.id}/tokens`);
      } else {
        router.push('/settings/profiles');
      }
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Failed to create profile'),
  });

  const validateAdo = async () => {
    setValidating(true);
    setError(null);
    try {
      const result = await validateAdoInline(form.ado_org_url, form.ado_pat);
      setAdoValidation(result);
      return result.valid;
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Validation failed';
      setAdoValidation({ valid: false, message: msg });
      return false;
    } finally {
      setValidating(false);
    }
  };

  const validateGh = async () => {
    setValidating(true);
    setError(null);
    try {
      const result = await validateGitHubInline(form.github_token, form.gh_org);
      setGhValidation(result);
      return result.valid;
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Validation failed';
      setGhValidation({ valid: false, message: msg });
      return false;
    } finally {
      setValidating(false);
    }
  };

  const goNext = async () => {
    if (step === 0) {
      if (!form.name || !form.ado_pat) {
        setError('Profile name and ADO PAT are required');
        return;
      }
      const ok = await validateAdo();
      if (ok) setStep(1);
      return;
    }
    if (step === 1) {
      if (!form.gh_org || !form.github_token) {
        setError('GitHub org and PAT are required');
        return;
      }
      const ok = await validateGh();
      if (ok) setStep(2);
    }
  };

  return (
    <div className="wizard-container">
      <div className="wizard-header">
        <h2 className="oai-subsection-title">
          {mode === 'onboarding' ? 'First deployment profile' : 'New migration profile'}
        </h2>
        {mode !== 'onboarding' && (
          <Link href="/settings/profiles" className="oai-button oai-button-secondary">
            Cancel
          </Link>
        )}
      </div>

      <div className="wizard-step-indicators">
        {STEPS.map((s, i) => {
          const Icon = s.icon;
          const active = i === step;
          const done = i < step;
          return (
            <div
              key={s.id}
              className={`wizard-step-indicator${active ? ' wizard-step-active' : ''}${done ? ' wizard-step-done' : ''}`}
            >
              <span className="wizard-step-icon">
                <Icon size={18} color={active || done ? '#35b8ff' : '#888'} />
              </span>
              <span className="wizard-step-label">{s.label}</span>
            </div>
          );
        })}
      </div>

      <div className="wizard-viewport">
        <div className="wizard-track" style={{ transform: `translateX(-${step * 100}%)` }}>
          {/* Step 1: Source */}
          <div className="wizard-slide oai-card">
            <h3 className="wizard-slide-title">
              <CloudIcon size={22} color="#35b8ff" /> Source — Azure DevOps
            </h3>
            <p className="form-hint">Connect to your ADO organization. Credentials are validated before proceeding.</p>
            <div className="form-grid">
              <div className="form-row">
                <label htmlFor="name">Profile name</label>
                <input
                  id="name"
                  className="oai-input"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. Contoso ADO → GitHub Enterprise"
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
                <label htmlFor="ado_pat">ADO PAT (required)</label>
                <input
                  id="ado_pat"
                  type="password"
                  className="oai-input"
                  value={form.ado_pat}
                  onChange={(e) => setForm({ ...form, ado_pat: e.target.value })}
                />
              </div>
            </div>
            {adoValidation && <ValidationResult valid={adoValidation.valid} message={adoValidation.message} />}
          </div>

          {/* Step 2: Target */}
          <div className="wizard-slide oai-card">
            <h3 className="wizard-slide-title">
              <GithubIcon size={22} color="#35b8ff" /> Target — GitHub
            </h3>
            <p className="form-hint">Configure the destination GitHub organization and at least one PAT.</p>
            <div className="form-grid">
              <div className="form-row">
                <label htmlFor="gh_org">GitHub organization</label>
                <input
                  id="gh_org"
                  className="oai-input"
                  value={form.gh_org}
                  onChange={(e) => setForm({ ...form, gh_org: e.target.value })}
                  placeholder="my-github-org"
                />
              </div>
              <div className="form-row">
                <label htmlFor="github_token">GitHub PAT (required)</label>
                <input
                  id="github_token"
                  type="password"
                  className="oai-input"
                  value={form.github_token}
                  onChange={(e) => setForm({ ...form, github_token: e.target.value })}
                />
              </div>
              <div className="form-row">
                <label htmlFor="token_name">Token label</label>
                <input
                  id="token_name"
                  className="oai-input"
                  value={form.github_token_name}
                  onChange={(e) => setForm({ ...form, github_token_name: e.target.value })}
                />
              </div>
            </div>
            {ghValidation && <ValidationResult valid={ghValidation.valid} message={ghValidation.message} />}
          </div>

          {/* Step 3: Confirm & Create */}
          <div className="wizard-slide oai-card">
            <h3 className="wizard-slide-title">
              <SearchIcon size={22} color="#35b8ff" /> Confirm &amp; create profile
            </h3>
            <p className="form-hint">
              {mode === 'settings-operator'
                ? 'Your profile will be submitted for admin approval before it can be used for migrations.'
                : 'Review the profile details, then create it. Scanning and phase assignment happen on the Discovery tab.'}
            </p>
            <div className="wizard-review-summary">
              <p><strong>{form.name}</strong></p>
              <p className="credential-meta">
                <span className="endpoint-label">Source</span> {form.ado_org_url}
              </p>
              <p className="credential-meta">
                <span className="endpoint-label">Target</span> {form.gh_org}
              </p>
              <p className="credential-meta">
                <span className="endpoint-label">Token label</span> {form.github_token_name}
              </p>
            </div>
          </div>
        </div>
      </div>

      {error && <p className="badge-manual" style={{ marginTop: 12 }}>{error}</p>}

      <div className="wizard-actions">
        {step > 0 && (
          <button
            type="button"
            className="oai-button oai-button-secondary"
            onClick={() => setStep(step - 1)}
            disabled={validating || setupMut.isPending}
          >
            Back
          </button>
        )}
        {step < 2 ? (
          <button
            type="button"
            className="oai-button oai-button-primary"
            onClick={goNext}
            disabled={validating}
          >
            {validating ? 'Validating…' : 'Next'}
          </button>
        ) : (
          <button
            type="button"
            className="oai-button oai-button-primary"
            disabled={setupMut.isPending || !adoValidation?.valid || !ghValidation?.valid}
            onClick={() => setupMut.mutate()}
          >
            {setupMut.isPending
              ? 'Creating…'
              : mode === 'settings-operator'
                ? 'Submit for approval'
                : 'Create profile'}
          </button>
        )}
      </div>
    </div>
  );
}
