'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { BoltIcon } from '@/components/Icons';
import { StepPipelineBar } from '@/components/PipelineProgress';
import {
  fetchDiscovery,
  fetchPipelineSteps,
  fetchSettings,
  startPipelineRun,
} from '@/lib/api';
import type { DiscoveryRepoItem, PipelineStep } from '@/lib/types';

export default function MigratePage() {
  return <MigrationView />;
}

function MigrationView() {
  const router = useRouter();

  // Migration state
  const [runName, setRunName] = useState('ADO migration run');
  const [dryRun, setDryRun] = useState(true);
  const [selectedSteps, setSelectedSteps] = useState<string[]>(['connect', 'migrate', 'validate']);
  const [previewSteps, setPreviewSteps] = useState<PipelineStep[]>([]);
  const [runError, setRunError] = useState<string | null>(null);

  // Repo selection state
  const [selectedRepoId, setSelectedRepoId] = useState('');
  const [repoQuery, setRepoQuery] = useState('');
  const [showRepoSuggestions, setShowRepoSuggestions] = useState(false);
  const repoInputRef = useRef<HTMLDivElement>(null);

  // Wave ID (optional — use for wave-based migration instead of repo)
  const [waveId, setWaveId] = useState('');

  // Dependency migration option (default: include dependencies)
  const [migrateDepsOnly, setMigrateDepsOnly] = useState(true);

  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const profileId = settings?.active_profile_id;

  const { data: stepDefs } = useQuery({
    queryKey: ['pipeline-steps', 'migrate'],
    queryFn: () => fetchPipelineSteps('migrate'),
  });

  useEffect(() => {
    if (stepDefs?.length) setSelectedSteps(stepDefs.map((s) => s.id));
  }, [stepDefs]);

  const { data: discovery } = useQuery({
    queryKey: ['discovery', profileId],
    queryFn: () => fetchDiscovery(profileId!),
    enabled: !!profileId,
  });
  const discoveredRepos = discovery?.repos ?? [];

  const hasTarget = !!(selectedRepoId || waveId.trim());

  // Start a pipeline run with selectable steps — redirects to /runs for live execution
  const startRunMut = useMutation({
    mutationFn: () =>
      startPipelineRun({
        name: runName,
        dry_run: dryRun,
        phase: '',
        wave_id: waveId.trim() ? Number(waveId.trim()) : null,
        steps: selectedSteps,
        repository_id: selectedRepoId || null,
        migrate_deps_only: migrateDepsOnly,
      }),
    onSuccess: (d) => router.push(`/?run=${d.run.id}`),
    onError: (err) => {
      setRunError(err instanceof Error ? err.message : 'Failed to start pipeline run');
    },
  });

  const toggleStep = (id: string) => {
    setSelectedSteps((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  };

  const [showPreview, setShowPreview] = useState(false);

  // Update preview steps when selected steps change while preview is visible
  useEffect(() => {
    if (showPreview && stepDefs) {
      const steps: PipelineStep[] = stepDefs.map((s) => ({
        ...s,
        status: selectedSteps.includes(s.id) ? 'pending' : 'skipped',
        message: '',
      }));
      setPreviewSteps(steps);
    }
  }, [selectedSteps, showPreview, stepDefs]);

  const buildPreview = () => {
    if (showPreview) {
      setShowPreview(false);
      return;
    }
    const steps: PipelineStep[] = (stepDefs ?? []).map((s) => ({
      ...s,
      status: selectedSteps.includes(s.id) ? 'pending' : 'skipped',
      message: '',
    }));
    setPreviewSteps(steps);
    setShowPreview(true);
  };

  return (
    <div>
      <h1 className="oai-page-title">Migrate</h1>
      <p>
        Execute migration for an assigned phase. Discovery and phase assignment live on the{' '}
        <a href="/settings/discovery">Discovery</a> tab.
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
              <a href="/settings/discovery">View discovery</a>
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

      {/* Migration Pipeline Section */}
      <div className="oai-card" style={{ marginTop: '1rem' }}>
        <div className="section-header">
          <span className="section-icon-svg"><BoltIcon size={24} color="#35b8ff" /></span>
          <h2 className="oai-subsection-title">Migration Pipeline</h2>
        </div>
        <p style={{ fontSize: 13, color: '#aaa', marginBottom: 16 }}>
          Select a repository or enter a wave ID, then execute a multi-step migration pipeline:
          connect credentials, migrate repositories, then validate.
          Each step shows live logs and can be skipped or retried.
        </p>

        <div className="content-layout">
          <div className="setup-sidebar">
            <div className="form-grid">
              <div className="form-row">
                <label>Run name</label>
                <input className="oai-input" value={runName} onChange={(e) => setRunName(e.target.value)} />
              </div>
              <div className="form-row" ref={repoInputRef} style={{ position: 'relative' }}>
                <label>ADO Repository</label>
                {discoveredRepos.length > 0 ? (
                  <RepoTypeahead
                    repos={discoveredRepos}
                    query={repoQuery}
                    showSuggestions={showRepoSuggestions}
                    onQueryChange={(q) => {
                      setRepoQuery(q);
                      setSelectedRepoId('');
                      setShowRepoSuggestions(true);
                    }}
                    onSelect={(repo) => {
                      setSelectedRepoId(`${repo.project}/${repo.repo_name}`);
                      setRepoQuery(`${repo.project}/${repo.repo_name}`);
                      setShowRepoSuggestions(false);
                    }}
                    onBlur={() => setShowRepoSuggestions(false)}
                  />
                ) : (
                  <p className="form-hint" style={{ margin: 0 }}>
                    No discovered repositories. Run a scan on the{' '}
                    <a href="/settings/discovery">Discovery</a> tab first.
                  </p>
                )}
              </div>
              <div className="form-row">
                <label>Wave ID (optional)</label>
                <input
                  className="oai-input"
                  placeholder="e.g. 123"
                  value={waveId}
                  onChange={(e) => setWaveId(e.target.value)}
                />
              </div>
              <div className="form-row form-row-checkbox">
                <label htmlFor="run-dry-run">
                  <input
                    id="run-dry-run"
                    type="checkbox"
                    checked={dryRun}
                    onChange={(e) => setDryRun(e.target.checked)}
                  />
                  Dry run
                </label>
              </div>
              {selectedRepoId && (
                <div className="form-row form-row-checkbox">
                  <label htmlFor="migrate-deps">
                    <input
                      id="migrate-deps"
                      type="checkbox"
                      checked={migrateDepsOnly}
                      onChange={(e) => setMigrateDepsOnly(e.target.checked)}
                    />
                    Include dependencies
                  </label>
                  {!migrateDepsOnly && (
                    <p className="form-hint" style={{ marginTop: 4, color: '#ffc107' }}>
                      Not recommended — migrating without dependencies may break the build.
                    </p>
                  )}
                </div>
              )}
            </div>

            <button
              type="button"
              className="oai-button oai-button-primary"
              style={{ marginTop: 16, width: '100%' }}
              disabled={startRunMut.isPending || !selectedSteps.length || !settings?.active_profile_id || !hasTarget}
              onClick={() => startRunMut.mutate()}
            >
              {startRunMut.isPending ? 'Starting…' : 'Start migration'}
            </button>
            {runError && (
              <p className="badge-manual" style={{ marginTop: 8 }}>{runError}</p>
            )}
            {!hasTarget && (
              <p className="form-hint" style={{ marginTop: 8 }}>
                Select a repository or enter a wave ID to start.
              </p>
            )}
            {!dryRun && (
              <p className="badge-manual" style={{ marginTop: 8 }}>
                ⚠️ Live migration will make actual changes.
              </p>
            )}
          </div>

          <div>
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
              {showPreview ? 'Hide preview' : 'Preview pipeline'}
            </button>

            {showPreview && previewSteps.length > 0 && <StepPipelineBar steps={previewSteps} />}
          </div>
        </div>
      </div>
    </div>
  );
}

interface RepoTypeaheadProps {
  repos: DiscoveryRepoItem[];
  query: string;
  showSuggestions: boolean;
  onQueryChange: (q: string) => void;
  onSelect: (repo: DiscoveryRepoItem) => void;
  onBlur: () => void;
}

function RepoTypeahead({
  repos,
  query,
  showSuggestions,
  onQueryChange,
  onSelect,
  onBlur,
}: RepoTypeaheadProps) {
  const [activeIndex, setActiveIndex] = useState(-1);
  const listRef = useRef<HTMLUListElement>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return repos.slice(0, 20);
    return repos
      .filter((r) => {
        const hay = `${r.project}/${r.repo_name}`.toLowerCase();
        return hay.includes(needle);
      })
      .slice(0, 20);
  }, [repos, query]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!showSuggestions || filtered.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => Math.min(prev + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIndex >= 0 && activeIndex < filtered.length) {
        onSelect(filtered[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onBlur();
    }
  };

  // Reset active index when query changes
  useEffect(() => {
    setActiveIndex(-1);
  }, [query]);

  // Scroll active item into view
  useEffect(() => {
    if (activeIndex < 0 || !listRef.current) return;
    const el = listRef.current.children[activeIndex] as HTMLElement | undefined;
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  return (
    <>
      <input
        className="oai-input"
        type="text"
        placeholder="Type to search ADO repos (e.g. my-project/my-repo)…"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => setTimeout(onBlur, 150)}
        autoComplete="off"
      />
      {showSuggestions && filtered.length > 0 && (
        <ul
          ref={listRef}
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            zIndex: 1000,
            maxHeight: 240,
            overflowY: 'auto',
            listStyle: 'none',
            margin: 0,
            padding: 0,
            border: '1px solid var(--border-color, #444)',
            borderRadius: 4,
            background: 'var(--card-bg, #1e1e1e)',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
        >
          {filtered.map((repo, i) => (
            <li
              key={`${repo.project}/${repo.repo_name}`}
              onMouseDown={(e) => {
                e.preventDefault();
                onSelect(repo);
              }}
              onMouseEnter={() => setActiveIndex(i)}
              style={{
                padding: '8px 12px',
                cursor: 'pointer',
                fontSize: 13,
                borderBottom: '1px solid var(--border-color, #333)',
                background:
                  i === activeIndex ? 'var(--hover-bg, #2a2a2a)' : undefined,
              }}
            >
              <strong>{repo.project}/{repo.repo_name}</strong>
              {repo.pipeline_count ? (
                <span style={{ marginLeft: 8, color: '#888' }}>
                  {repo.pipeline_count} pipeline{repo.pipeline_count === 1 ? '' : 's'}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {showSuggestions && query.trim() && filtered.length === 0 && (
        <p className="form-hint" style={{ margin: '4px 0 0', fontSize: 12 }}>
          No repos match &ldquo;{query}&rdquo;
        </p>
      )}
    </>
  );
}

