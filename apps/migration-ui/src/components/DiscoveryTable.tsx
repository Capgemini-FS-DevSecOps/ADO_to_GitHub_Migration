'use client';

import { useEffect, useMemo, useState } from 'react';
import type { DiscoveryRepoItem } from '@/lib/types';

type RowState = DiscoveryRepoItem & { key: string };

const PAGE_SIZE = 25;

export function DiscoveryTable({ repos }: { repos: DiscoveryRepoItem[] }) {
  const rows = useMemo<RowState[]>(
    () => repos.map((r) => ({ ...r, key: `${r.project}/${r.repo_name}` })),
    [repos],
  );
  const [filter, setFilter] = useState('');
  const [projectFilter, setProjectFilter] = useState('');
  const [page, setPage] = useState(0);

  const projects = useMemo(() => {
    const set = new Set<string>();
    rows.forEach((r) => set.add(r.project));
    return Array.from(set).sort();
  }, [rows]);

  const filtered = rows.filter((r) => {
    if (projectFilter && r.project !== projectFilter) return false;
    const q = filter.toLowerCase();
    return (
      !q ||
      r.project.toLowerCase().includes(q) ||
      r.repo_name.toLowerCase().includes(q)
    );
  });

  useEffect(() => {
    setPage(0);
  }, [rows, filter, projectFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  return (
    <div className="discovery-table-wrap">
      <div className="discovery-toolbar">
        <input
          className="oai-input"
          placeholder="Filter repos…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <select
          className="oai-input"
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)}
          style={{ maxWidth: 200 }}
        >
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      <div className="discovery-table-scroll">
        <table className="discovery-table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Repository</th>
              <th>Risk</th>
              <th>Pipelines</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((r) => (
              <tr key={r.key}>
                <td>{r.project}</td>
                <td>
                  <code>{r.repo_name}</code>
                  {r.gh_repo && r.gh_repo !== r.repo_name && (
                    <span className="discovery-gh-target"> → {r.gh_org}/{r.gh_repo}</span>
                  )}
                </td>
                <td>{r.total_score.toFixed(0)}</td>
                <td>{r.pipeline_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="discovery-empty">No repos match the current filters.</p>
        )}
      </div>
      {filtered.length > PAGE_SIZE && (
        <div className="history-pagination">
          <span className="form-hint">
            {filtered.length} repo{filtered.length === 1 ? '' : 's'}
            {' · '}
            Page {page + 1} of {totalPages}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </button>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
      <p className="discovery-footnote">
        {filtered.length} repo(s){projectFilter ? ` in ${projectFilter}` : ''} · Risk score and pipeline count per repository.
      </p>
    </div>
  );
}
