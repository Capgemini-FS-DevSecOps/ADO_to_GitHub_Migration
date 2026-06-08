'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { BoltIcon } from '@/components/Icons';
import { StepPipelineBar } from '@/components/PipelineProgress';
import {
  fetchPipelineSteps,
  fetchSettings,
  startPipelineRun,
} from '@/lib/api';
import type { PipelineStep } from '@/lib/types';

const MIGRATE_STEP_IDS = ['connect', 'migrate', 'validate'];

export default function MigratePage() {
  const router = useRouter();
  const [name, setName] = useState('ADO migration run');
  const [dryRun, setDryRun] = useState(true);
  const [phase, setPhase] = useState('poc');
  const [waveId, setWaveId] = useState<string>('');
  const [selectedSteps, setSelectedSteps] = useState<string[]>(MIGRATE_STEP_IDS);
  const [previewSteps, setPreviewSteps] = useState<PipelineStep[]>([]);

  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const phaseOptions = settings?.advanced.phases ?? [];

  useEffect(() => {
    if (settings?.advanced.default_phase) setPhase(settings.advanced.default_phase);
  }, [settings?.advanced.default_phase]);
  const { data: stepDefs } = useQuery({
    queryKey: ['pipeline-steps', 'migrate'],
    queryFn: () => fetchPipelineSteps('migrate'),
  });

  useEffect(() => {
    if (stepDefs?.length) {
      setSelectedSteps(stepDefs.map((s) => s.id));
    }
  }, [stepDefs]);

  const startMut = useMutation({
    mutationFn: () =>
      startPipelineRun({
        name,
        dry_run: dryRun,
        phase,
        wave_id: waveId ? Number(waveId) : null,
        steps: selectedSteps,
      }),
    onSuccess: (d) => router.push(`/runs?id=${d.run.id}`),
  });

  const toggleStep = (id: string) => {
    setSelectedSteps((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  };

  const buildPreview = () => {
    const steps: PipelineStep[] = (stepDefs ?? []).map((s, i) => ({
      ...s,
      status: selectedSteps.includes(s.id)
        ? (i === 0 ? 'running' : 'pending')
        : 'skipped',
      message: '',
    }));
    setPreviewSteps(steps);
  };

  return (
    <div>
      <h1 className="oai-page-title">Migrate</h1>
      <p>
        Execute migration for an assigned phase. Discovery and phase assignment live on the{' '}
        <a href="/discovery">Discovery</a> tab.
      </p>

      {settings?.active_profile_id && (() => {
        const active = settings.migration_profiles?.find((p) => p.id === settings.active_profile_id);
        if (!active) return null;
        return (
          <div className="oai-card" style={{ marginBottom: '1rem' }}>
            <p style={{ margin: 0, fontSize: 13 }}>
              Active migration profile: <strong>{active.name}</strong>
              {' '}({active.ado_org_url} → {active.gh_org})
              {' · '}
              <a href="/discovery">View discovery</a>
              {' · '}
              <a href={`/settings/profiles/${active.id}/source`}>Change profile</a>
            </p>
          </div>
        );
      })()}

      {!settings?.active_profile_id && (
        <div className="oai-card oai-welcome-card" style={{ marginBottom: '1rem' }}>
          <p>
            No active migration profile —{' '}
            <a href="/settings/profiles">create or activate one</a> before running.
          </p>
        </div>
      )}

      <div className="content-layout">
        <div className="setup-sidebar">
          <div className="oai-card">
            <h2 className="oai-subsection-title">Run configuration</h2>
            <div className="form-grid">
              <div className="form-row">
                <label>Run name</label>
                <input className="oai-input" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="form-row">
                <label>Phase</label>
                <select className="oai-input" value={phase} onChange={(e) => setPhase(e.target.value)}>
                  {(phaseOptions.length ? phaseOptions : [{ id: 'poc', name: 'POC', order: 0 }]).map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div className="form-row">
                <label>Wave ID (optional)</label>
                <input
                  className="oai-input"
                  placeholder="Leave empty for phase run"
                  value={waveId}
                  onChange={(e) => setWaveId(e.target.value)}
                />
              </div>
              <div className="form-row form-row-checkbox">
                <label htmlFor="migrate-dry-run">
                  <input
                    id="migrate-dry-run"
                    type="checkbox"
                    checked={dryRun}
                    onChange={(e) => setDryRun(e.target.checked)}
                  />
                  Dry run
                </label>
              </div>
            </div>

            <button
              type="button"
              className="oai-button oai-button-primary"
              style={{ marginTop: 16, width: '100%' }}
              disabled={startMut.isPending || !selectedSteps.length || !settings?.active_profile_id}
              onClick={() => startMut.mutate()}
            >
              {startMut.isPending ? 'Starting…' : 'Start migration'}
            </button>
            {startMut.isError && (
              <p className="badge-manual" style={{ marginTop: 8 }}>{String(startMut.error)}</p>
            )}
          </div>
        </div>

        <div>
          <div className="oai-card">
            <div className="section-header">
              <span className="section-icon-svg"><BoltIcon size={24} color="#35b8ff" /></span>
              <h2 className="section-title">Migration steps</h2>
            </div>
            <p style={{ marginBottom: 16 }}>
              Connect credentials, run the migration, then validate commit SHAs.
            </p>

            <ul className="accelerator-step-list">
              {(stepDefs ?? []).map((step) => (
                <li key={step.id}>
                  <label className="accelerator-step-row">
                    <input
                      type="checkbox"
                      className="accelerator-step-check"
                      checked={selectedSteps.includes(step.id)}
                      onChange={() => toggleStep(step.id)}
                    />
                    <div className="accelerator-step-body">
                      <strong>{step.label}</strong>
                      <p>{step.description}</p>
                    </div>
                  </label>
                </li>
              ))}
            </ul>

            <button type="button" className="oai-button oai-button-secondary" style={{ marginTop: 16 }} onClick={buildPreview}>
              Preview pipeline bar
            </button>

            {previewSteps.length > 0 && <StepPipelineBar steps={previewSteps} />}
          </div>

          <div className="oai-card ai-recommendation-card">
            <h2 className="ai-recommendation-title">Before you migrate</h2>
            <ol className="ai-recommendation-checklist">
              <li>Complete discovery and confirm phase assignments on the Discovery tab</li>
              <li>Start with POC and dry-run enabled</li>
              <li>Monitor progress on the Monitor tab</li>
              <li>Run Validation after migration completes</li>
            </ol>
          </div>
        </div>
      </div>
    </div>
  );
}
