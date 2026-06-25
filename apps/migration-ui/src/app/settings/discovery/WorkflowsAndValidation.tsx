'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useRef, useState } from 'react';
import { UnifiedSubNavigation } from '@/components/UnifiedNavigation';
import { DISCOVERY_SUB_TABS } from '@/lib/navigationState';
import {
  fetchPipelineRuns,
  fetchReadiness,
  fetchSettings,
  runValidation,
} from '@/lib/api';

export function WorkflowsView() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const { data: readiness, isLoading: readinessLoading } = useQuery({
    queryKey: ['readiness'],
    queryFn: () => fetchReadiness(),
    retry: false,
  });
  const { data: runsData, isLoading: runsLoading } = useQuery({
    queryKey: ['pipeline-runs'],
    queryFn: () => fetchPipelineRuns(),
  });

  const runs = runsData?.runs ?? [];
  const profileId = settings?.active_profile_id;
  const active = settings?.migration_profiles?.find((p) => p.id === profileId);

  return (
    <div>
      <UnifiedSubNavigation tabs={DISCOVERY_SUB_TABS} />

      <h1 className="oai-page-title">Workflow Review</h1>
      <p>
        Pipeline conversion readiness and recent migration runs that include workflow transformation.
      </p>

      {profileId && active && (
        <p className="form-hint" style={{ marginBottom: 16 }}>
          Active profile: <strong>{active.name}</strong>
        </p>
      )}

      <div className="oai-card" style={{ marginBottom: '1rem' }}>
        <h2 className="oai-subsection-title">Pipeline readiness</h2>
        {readinessLoading && <p className="oai-loading">Loading inventory…</p>}
        {!readinessLoading && readiness && (
          <div className="metrics-grid">
            <div className="metric-card">
              <div className="metric-label">Auto-convert</div>
              <div className="metric-value badge-auto">{readiness.auto ?? 0}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Assisted</div>
              <div className="metric-value badge-assisted">{readiness.assisted ?? 0}</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Manual</div>
              <div className="metric-value badge-manual">{readiness.manual ?? 0}</div>
            </div>
          </div>
        )}
        {!readinessLoading && !readiness && (
          <p>
            No pipeline inventory yet. Run a full pipeline scan from{' '}
            <Link href="/settings/migrate">Migrate</Link> (include inventory step) or{' '}
            <code>ado2gh pipelines inventory</code>.
          </p>
        )}
      </div>

      <div className="oai-card" style={{ marginBottom: '1rem' }}>
        <h2 className="oai-subsection-title">Generated workflow output</h2>
        <p>
          Converted GitHub Actions YAML is written under{' '}
          <code>output/workflows/&lt;org&gt;/&lt;repo&gt;/.github/workflows/</code> on the accelerator
          host (or shared <code>ADO2GH_DATA_DIR</code> volume in Docker).
        </p>
        <p>Compare ADO source YAML with generated GHA before approving push-workflows.</p>
      </div>

      <div className="oai-card">
        <h2 className="oai-subsection-title">Recent pipeline runs</h2>
        {runsLoading && <p className="oai-loading">Loading runs…</p>}
        {!runsLoading && runs.length === 0 && (
          <p>
            No runs yet. Start one from <Link href="/settings/migrate">Migrate</Link> or monitor on{' '}
            <Link href="/runs">Runs</Link>.
          </p>
        )}
        {!runsLoading && runs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.slice(0, 20).map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link href={`/runs?id=${r.id}`}>{r.name}</Link>
                  </td>
                  <td>{r.status}</td>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function ValidationView() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const profileId = settings?.active_profile_id;
  const activeProfile = settings?.migration_profiles?.find((p) => p.id === profileId);

  const [repoListText, setRepoListText] = useState('');
  const [configYaml, setConfigYaml] = useState('');
  const [repoFileName, setRepoFileName] = useState<string | null>(null);
  const [configFileName, setConfigFileName] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const repoFileRef = useRef<HTMLInputElement>(null);
  const configFileRef = useRef<HTMLInputElement>(null);

  const validateMut = useMutation({
    mutationFn: () =>
      runValidation({
        profile_id: profileId ?? undefined,
        phase: undefined,
        input_text: repoListText.trim() || undefined,
        config_yaml: configYaml.trim() || undefined,
      }),
  });

  const result = validateMut.data;

  const onRepoFile = async (file: File | undefined) => {
    if (!file) return;
    setRepoFileName(file.name);
    setRepoListText(await readFileAsText(file));
  };

  const onConfigFile = async (file: File | undefined) => {
    if (!file) return;
    setConfigFileName(file.name);
    setConfigYaml(await readFileAsText(file));
  };

  return (
    <div>
      <UnifiedSubNavigation tabs={DISCOVERY_SUB_TABS} />

      <h1 className="oai-page-title">Validation</h1>
      <p>
        Commit-level SHA verification — compares source ADO repos against GitHub targets after migration.
      </p>

      <div className="content-layout">
        <div className="setup-sidebar">
          <div className="oai-card">
            <h2 className="oai-subsection-title">Run validation</h2>
            <p style={{ fontSize: 13, color: '#aaa', marginBottom: 16 }}>
              Repos are loaded from the active profile&apos;s discovery database by default. Upload
              files only when you need a custom repo list or migration.yaml override.
            </p>

            <div className="form-grid">
              <div className="form-row">
                <label>Active profile</label>
                <input
                  className="oai-input"
                  readOnly
                  value={activeProfile?.name ?? (profileId ? profileId : 'No active profile')}
                />
              </div>
              <div className="form-row">
                <label>Repo list file (optional)</label>
                <input
                  ref={repoFileRef}
                  type="file"
                  accept=".txt,.csv,text/plain,text/csv"
                  className="oai-input"
                  onChange={(e) => onRepoFile(e.target.files?.[0])}
                />
                {repoFileName && (
                  <p style={{ fontSize: 11, color: '#888', marginTop: 4 }}>
                    Loaded {repoFileName}
                  </p>
                )}
              </div>
              <div className="form-row">
                <label>migration.yaml (optional override)</label>
                <input
                  ref={configFileRef}
                  type="file"
                  accept=".yaml,.yml,text/yaml,application/x-yaml"
                  className="oai-input"
                  onChange={(e) => onConfigFile(e.target.files?.[0])}
                />
                {configFileName && (
                  <p style={{ fontSize: 11, color: '#888', marginTop: 4 }}>
                    Loaded {configFileName}
                  </p>
                )}
              </div>
            </div>

            <button
              type="button"
              className="oai-button oai-button-secondary"
              style={{ marginTop: 12, width: '100%', fontSize: 11 }}
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? 'Hide' : 'Show'} advanced (local CLI paths)
            </button>

            {showAdvanced && (
              <p style={{ fontSize: 12, color: '#888', marginTop: 12 }}>
                State is read from the configured storage backend (PostgreSQL in Docker prod, SQLite
                locally). File paths below only apply when running the accelerator on the same host
                as the files.
              </p>
            )}

            <button
              type="button"
              className="oai-button oai-button-primary"
              style={{ marginTop: 16, width: '100%' }}
              disabled={validateMut.isPending || !profileId}
              onClick={() => validateMut.mutate()}
            >
              {validateMut.isPending ? 'Validating…' : 'Run validation'}
            </button>
            {!profileId && (
              <p className="badge-manual" style={{ marginTop: 8 }}>
                Activate a migration profile under Settings before validating.
              </p>
            )}
            {validateMut.isError && (
              <p className="badge-manual" style={{ marginTop: 8 }}>
                {String(validateMut.error)}
              </p>
            )}
          </div>
        </div>

        <div>
          {result && (
            <div className="oai-card">
              <h2 className="oai-subsection-title">Results</h2>
              <div className="validation-summary">
                <div className="validation-stat">
                  <span className="validation-stat-value">{result.total}</span>
                  <span className="validation-stat-label">Total</span>
                </div>
                <div className="validation-stat validation-stat-pass">
                  <span className="validation-stat-value">{result.matched}</span>
                  <span className="validation-stat-label">Matched</span>
                </div>
                <div className="validation-stat validation-stat-fail">
                  <span className="validation-stat-value">{result.failed}</span>
                  <span className="validation-stat-label">Failed</span>
                </div>
              </div>

              {result.results.length > 0 && (
                <div className="discovery-table-scroll" style={{ marginTop: 16 }}>
                  <table className="discovery-table">
                    <thead>
                      <tr>
                        <th>Project</th>
                        <th>Repo</th>
                        <th>Status</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.results.map((row) => (
                        <tr
                          key={`${row.project}-${row.repo}`}
                          className={row.overall === 'PASS' ? 'validation-row-pass' : 'validation-row-fail'}
                        >
                          <td>{row.project || '—'}</td>
                          <td><code>{row.repo || '—'}</code></td>
                          <td>{row.overall}</td>
                          <td>
                            {row.primary_reason ?? row.message ?? row.detail ?? '—'}
                            {row.gh_target ? (
                              <div className="history-cell-muted" style={{ fontSize: 11, marginTop: 4 }}>
                                GitHub: {row.gh_target}
                              </div>
                            ) : null}
                            {row.checks?.some((c) => c.verdict === 'FAIL' || c.verdict === 'WARN') && (
                              <ul className="run-detail-scope-list" style={{ marginTop: 6 }}>
                                {row.checks
                                  .filter((c) => c.verdict === 'FAIL' || c.verdict === 'WARN')
                                  .map((c) => (
                                    <li key={c.check}>
                                      {c.check}: {c.detail}
                                    </li>
                                  ))}
                              </ul>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {!result && !validateMut.isPending && (
            <div className="oai-card ai-recommendation-card">
              <p>
                Run validation after a migration wave completes. The accelerator compares HEAD commit
                SHAs between ADO and GitHub for all discovered repositories.
              </p>
              {activeProfile && (
                <p style={{ marginTop: 12 }}>
                  Using profile <strong>{activeProfile.name}</strong> and storage backend configured
                  in the accelerator environment (not host file paths).
                </p>
              )}
              <p style={{ marginTop: 12 }}>
                After a failed migrate run, check <a href="/runs">Runs</a> for per-repo error detail.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read file'));
    reader.readAsText(file);
  });
}
