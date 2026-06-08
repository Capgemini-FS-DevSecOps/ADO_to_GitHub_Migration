'use client';

import type { PipelineStep } from '@/lib/types';

interface RepoMigrateDetail {
  repo: string;
  status: string;
  summary?: string;
  errors?: string[];
  scopes?: Array<{ scope: string; status: string; error?: string; detail?: string }>;
}

interface RepoValidateDetail {
  project: string;
  repo: string;
  gh_target: string;
  overall: string;
  primary_reason?: string;
  checks?: Array<{ check: string; verdict: string; detail?: string }>;
}

function verdictClass(verdict: string): string {
  if (verdict === 'PASS' || verdict === 'completed') return 'badge-auto';
  if (verdict === 'WARN' || verdict === 'partial') return 'badge-assisted';
  return 'badge-manual';
}

function MigrateResults({ step }: { step: PipelineStep }) {
  const details = (step.result?.repo_details as RepoMigrateDetail[] | undefined) ?? [];
  const repos = step.result?.repos as Record<string, unknown> | undefined;
  if (!details.length && repos) {
    return (
      <p style={{ fontSize: 12, color: '#888' }}>
        {Object.keys(repos).length} repo(s) — expand logs for per-repo lines.
      </p>
    );
  }
  if (!details.length) return null;

  return (
    <table className="run-detail-table">
      <thead>
        <tr>
          <th>Repo</th>
          <th>Status</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {details.map((d) => (
          <tr key={d.repo}>
            <td>{d.repo}</td>
            <td>
              <span className={verdictClass(d.status)}>{d.status}</span>
            </td>
            <td>
              {d.errors?.length ? d.errors.join('; ') : d.summary ?? '—'}
              {d.scopes?.some((s) => s.status !== 'completed') && (
                <ul className="run-detail-scope-list">
                  {d.scopes
                    ?.filter((s) => s.status !== 'completed')
                    .map((s) => (
                      <li key={s.scope}>
                        {s.scope}: {s.error || s.detail || 'failed'}
                      </li>
                    ))}
                </ul>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ValidateResults({ step }: { step: PipelineStep }) {
  const details = (step.result?.repo_details as RepoValidateDetail[] | undefined) ?? [];
  if (!details.length) return null;

  return (
    <table className="run-detail-table">
      <thead>
        <tr>
          <th>Repo</th>
          <th>GitHub</th>
          <th>Result</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {details.map((d) => (
          <tr key={`${d.project}/${d.repo}`}>
            <td>
              {d.project}/{d.repo}
            </td>
            <td>{d.gh_target}</td>
            <td>
              <span className={verdictClass(d.overall)}>{d.overall}</span>
            </td>
            <td>
              {d.primary_reason ?? '—'}
              {d.checks?.some((c) => c.verdict === 'FAIL' || c.verdict === 'WARN') && (
                <ul className="run-detail-scope-list">
                  {d.checks
                    ?.filter((c) => c.verdict === 'FAIL' || c.verdict === 'WARN')
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
  );
}

export function RunStepDetails({ steps }: { steps: PipelineStep[] }) {
  const migrate = steps.find((s) => s.id === 'migrate');
  const validate = steps.find((s) => s.id === 'validate');
  const hasMigrate = migrate?.result?.repo_details || migrate?.result?.repos;
  const hasValidate = validate?.result?.repo_details;

  if (!hasMigrate && !hasValidate) return null;

  return (
    <div className="oai-card">
      <h2 className="oai-subsection-title">Repo results</h2>
      {hasMigrate && migrate && (
        <section style={{ marginBottom: 24 }}>
          <h3 className="oai-detail-heading">Migration</h3>
          <p style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>{migrate.message}</p>
          <MigrateResults step={migrate} />
        </section>
      )}
      {hasValidate && validate && (
        <section>
          <h3 className="oai-detail-heading">Validation</h3>
          <p style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>{validate.message}</p>
          <ValidateResults step={validate} />
        </section>
      )}
    </div>
  );
}
