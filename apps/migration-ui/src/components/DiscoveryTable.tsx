'use client';

import { useEffect, useMemo, useState } from 'react';
import type { DiscoveryRepoItem } from '@/lib/types';

type RowState = DiscoveryRepoItem & { key: string };

const PAGE_SIZE = 25;

export function DiscoveryTable({
  repos,
  onCreateWave,
  creatingWave,
}: {
  repos: DiscoveryRepoItem[];
  onCreateWave: (name: string, repositoryIds: string[]) => void;
  creatingWave?: boolean;
}) {
  const initial = useMemo(
    () => repos.map((r) => ({ ...r, key: `${r.project}/${r.repo_name}` })),
    [repos],
  );
  const [rows, setRows] = useState<RowState[]>(initial);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [projectFilter, setProjectFilter] = useState('');
  const [page, setPage] = useState(0);
  const [waveName, setWaveName] = useState('');
  const [showWaveForm, setShowWaveForm] = useState(false);

  useEffect(() => {
    setRows(initial);
    setSelected(new Set());
    setPage(0);
  }, [initial]);

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
  }, [filter, projectFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageItems = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  const toggleAll = () => {
    const keys = new Set(filtered.map((r) => r.key));
    const allSelected = filtered.every((r) => selected.has(r.key));
    setSelected(allSelected ? new Set() : keys);
  };

  const toggleRow = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectedIds = useMemo(
    () =>
      rows
        .filter((r) => selected.has(r.key))
        .map((r) => `${r.project}/${r.repo_name}`),
    [rows, selected],
  );

  const handleCreateWave = () => {
    if (!waveName.trim() || selectedIds.length === 0) return;
    onCreateWave(waveName.trim(), selectedIds);
    setWaveName('');
    setShowWaveForm(false);
    setSelected(new Set());
  };

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
        <button type="button" className="oai-button oai-button-secondary" onClick={toggleAll}>
          {filtered.every((r) => selected.has(r.key)) ? 'Clear selection' : 'Select all'}
        </button>
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={selectedIds.length === 0}
          onClick={() => setShowWaveForm((v) => !v)}
        >
          {selectedIds.length === 0 ? 'Create wave' : `Create wave (${selectedIds.length})`}
        </button>
      </div>

      {showWaveForm && (
        <div className="oai-card" style={{ marginBottom: 16, padding: 16 }}>
          <div className="form-row">
            <label>Wave name</label>
            <input
              className="oai-input"
              placeholder="e.g. Q1 Backend Services Wave"
              value={waveName}
              onChange={(e) => setWaveName(e.target.value)}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button
              type="button"
              className="oai-button oai-button-primary"
              disabled={!waveName.trim() || selectedIds.length === 0 || creatingWave}
              onClick={handleCreateWave}
            >
              {creatingWave ? 'Creating…' : 'Create wave'}
            </button>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              onClick={() => setShowWaveForm(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="discovery-table-scroll">
        <table className="discovery-table">
          <thead>
            <tr>
              <th style={{ width: 40 }}>
                <input
                  type="checkbox"
                  checked={filtered.length > 0 && filtered.every((r) => selected.has(r.key))}
                  onChange={toggleAll}
                />
              </th>
              <th>Project</th>
              <th>Repository</th>
              <th>Risk</th>
              <th>Pipelines</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((r) => (
              <tr key={r.key}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(r.key)}
                    onChange={() => toggleRow(r.key)}
                  />
                </td>
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
        {filtered.length} repo(s){projectFilter ? ` in ${projectFilter}` : ''} · Risk score only — select repos and create a wave to migrate them.
      </p>
    </div>
  );
}
