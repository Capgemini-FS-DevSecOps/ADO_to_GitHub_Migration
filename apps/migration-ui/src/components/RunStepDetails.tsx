'use client';

import { useMemo, useState } from 'react';
import type { PipelineStep } from '@/lib/types';

interface RepoMigrateDetail {
  repo: string;
  status: string;
  summary?: string;
  errors?: string[];
  scopes?: Array<{
    scope: string;
    label?: string;
    category?: string;
    status: string;
    error?: string;
    detail?: string;
  }>;
}

interface WorkItemDetail {
  id: string;
  label: string;
  category?: string;
  category_label?: string;
  status: string;
  blocker?: string;
  detail?: string;
  repo?: string;
  scope?: string;
}

interface RepoValidateDetail {
  project: string;
  repo: string;
  gh_target: string;
  overall: string;
  primary_reason?: string;
  checks?: Array<{ check: string; verdict: string; detail?: string }>;
}

const MIGRATE_STEP_IDS = new Set([
  'migrate',
  'migrate_repos',
  'convert_pipelines',
  'map_secrets',
  'convert_metadata',
]);

/** Scopes executed per pipeline step (null = all scopes). */
const STEP_SCOPES: Record<string, string[] | null> = {
  migrate_repos: ['repo'],
  convert_pipelines: ['pipelines'],
  map_secrets: ['secrets'],
  convert_metadata: ['branch_policies', 'wiki', 'work_items'],
  migrate: null,
};

const WORK_ITEM_PAGE_SIZE = 15;

function verdictClass(verdict: string): string {
  if (verdict === 'PASS' || verdict === 'completed') return 'badge-auto';
  if (verdict === 'WARN' || verdict === 'partial') return 'badge-assisted';
  if (verdict === 'blocked' || verdict === 'ready') return 'badge-assisted';
  return 'badge-manual';
}

function workItemsForStep(step: PipelineStep): WorkItemDetail[] {
  const items = (step.result?.work_items as WorkItemDetail[] | undefined) ?? [];
  const scopes = STEP_SCOPES[step.id];
  if (!scopes) return items;
  return items.filter((wi) => wi.scope && scopes.includes(wi.scope));
}

function WorkItemTable({ items, stepLabel }: { items: WorkItemDetail[]; stepLabel: string }) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [failuresOnly, setFailuresOnly] = useState(false);

  const failureCount = items.filter((wi) => wi.status === 'failed' || wi.status === 'blocked').length;

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return items.filter((wi) => {
      if (failuresOnly && wi.status !== 'failed' && wi.status !== 'blocked') return false;
      if (!needle) return true;
      const hay = [wi.label, wi.repo, wi.status, wi.blocker, wi.detail, wi.category_label]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [items, search, failuresOnly]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / WORK_ITEM_PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageItems = filtered.slice(
    safePage * WORK_ITEM_PAGE_SIZE,
    safePage * WORK_ITEM_PAGE_SIZE + WORK_ITEM_PAGE_SIZE,
  );

  if (!items.length) return null;

  return (
    <div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 8,
          alignItems: 'center',
          marginBottom: 10,
        }}
      >
        <input
          type="search"
          className="oai-input"
          placeholder={`Search ${stepLabel.toLowerCase()}…`}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          style={{ flex: '1 1 180px', minWidth: 160, fontSize: 12, padding: '6px 10px' }}
        />
        {failureCount > 0 && (
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              checked={failuresOnly}
              onChange={(e) => {
                setFailuresOnly(e.target.checked);
                setPage(0);
              }}
            />
            Failures only ({failureCount})
          </label>
        )}
        <span style={{ fontSize: 11, color: '#888' }}>
          {filtered.length} of {items.length} repo(s)
        </span>
      </div>
      <table className="run-detail-table">
        <thead>
          <tr>
            <th>Repository</th>
            <th>Work item</th>
            <th>Status</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {pageItems.map((wi) => (
            <tr key={wi.id}>
              <td>{wi.repo ?? '—'}</td>
              <td>{wi.label}</td>
              <td>
                <span className={verdictClass(wi.status)}>{wi.status}</span>
              </td>
              <td>{wi.blocker || wi.detail || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {filtered.length === 0 && (
        <p style={{ fontSize: 12, color: '#888', marginTop: 8 }}>No rows match your filter.</p>
      )}
      {totalPages > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: 10,
            gap: 8,
          }}
        >
          <button
            type="button"
            className="oai-button oai-button-secondary"
            style={{ padding: '4px 10px', fontSize: 11 }}
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span style={{ fontSize: 11, color: '#888' }}>
            Page {safePage + 1} of {totalPages}
          </span>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            style={{ padding: '4px 10px', fontSize: 11 }}
            disabled={safePage + 1 >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function MigrateResults({ step }: { step: PipelineStep }) {
  const details = (step.result?.repo_details as RepoMigrateDetail[] | undefined) ?? [];
  const workItems = workItemsForStep(step);
  const repos = step.result?.repos as Record<string, unknown> | undefined;

  if (workItems.length > 0) {
    return <WorkItemTable items={workItems} stepLabel={step.label} />;
  }

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
                        {s.label ?? s.scope}: {s.error || s.detail || 'failed'}
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
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const details = (step.result?.repo_details as RepoValidateDetail[] | undefined) ?? [];

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return details;
    return details.filter((d) =>
      [d.project, d.repo, d.gh_target, d.overall, d.primary_reason]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [details, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / WORK_ITEM_PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageItems = filtered.slice(
    safePage * WORK_ITEM_PAGE_SIZE,
    safePage * WORK_ITEM_PAGE_SIZE + WORK_ITEM_PAGE_SIZE,
  );

  if (!details.length) return null;

  return (
    <div>
      {details.length > WORK_ITEM_PAGE_SIZE && (
        <input
          type="search"
          className="oai-input"
          placeholder="Search validation results…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          style={{ width: '100%', fontSize: 12, padding: '6px 10px', marginBottom: 10 }}
        />
      )}
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
          {pageItems.map((d) => (
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
      {totalPages > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: 10,
            gap: 8,
          }}
        >
          <button
            type="button"
            className="oai-button oai-button-secondary"
            style={{ padding: '4px 10px', fontSize: 11 }}
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span style={{ fontSize: 11, color: '#888' }}>
            Page {safePage + 1} of {totalPages} ({filtered.length} repos)
          </span>
          <button
            type="button"
            className="oai-button oai-button-secondary"
            style={{ padding: '4px 10px', fontSize: 11 }}
            disabled={safePage + 1 >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

export function RunStepDetails({ steps }: { steps: PipelineStep[] }) {
  const migrateSteps = steps.filter((s) => {
    if (!MIGRATE_STEP_IDS.has(s.id)) return false;
    if (s.result?.repo_details || s.result?.repos) return true;
    return workItemsForStep(s).length > 0;
  });
  const validate = steps.find((s) => s.id === 'validate');
  const hasMigrate = migrateSteps.length > 0;
  const hasValidate = Boolean(validate?.result?.repo_details);

  if (!hasMigrate && !hasValidate) return null;

  return (
    <div className="oai-card">
      <h2 className="oai-subsection-title">Repo results</h2>
      {hasMigrate &&
        migrateSteps.map((step) => (
          <section key={step.id} style={{ marginBottom: 24 }}>
            <h3 className="oai-detail-heading">{step.label}</h3>
            <p style={{ fontSize: 12, color: '#888', marginBottom: 8, whiteSpace: 'pre-wrap' }}>{step.message}</p>
            <MigrateResults step={step} />
          </section>
        ))}
      {hasValidate && validate && (
        <section>
          <h3 className="oai-detail-heading">Validation</h3>
          <p style={{ fontSize: 12, color: '#888', marginBottom: 8, whiteSpace: 'pre-wrap' }}>{validate.message}</p>
          <ValidateResults step={validate} />
        </section>
      )}
    </div>
  );
}
