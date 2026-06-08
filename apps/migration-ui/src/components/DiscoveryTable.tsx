'use client';

import { useEffect, useMemo, useState } from 'react';
import type { DiscoveryRepoItem, PhaseDefinition } from '@/lib/types';

type RowState = DiscoveryRepoItem & { key: string };

export function DiscoveryTable({
  repos,
  phases,
  onSave,
  saving,
}: {
  repos: DiscoveryRepoItem[];
  phases: PhaseDefinition[];
  onSave: (assignments: { project: string; repo_name: string; assigned_phase: string }[]) => void;
  saving?: boolean;
}) {
  const sortedPhases = useMemo(
    () => [...phases].sort((a, b) => a.order - b.order),
    [phases],
  );
  const defaultPhase = sortedPhases[0]?.id ?? 'poc';
  const labelFor = (id: string) => sortedPhases.find((p) => p.id === id)?.name ?? id;

  const initial = useMemo(
    () =>
      repos.map((r) => ({
        ...r,
        key: `${r.project}/${r.repo_name}`,
        assigned_phase: r.assigned_phase ?? r.suggested_phase ?? defaultPhase,
      })),
    [repos, defaultPhase],
  );
  const [rows, setRows] = useState<RowState[]>(initial);
  const [filter, setFilter] = useState('');
  const [phaseFilter, setPhaseFilter] = useState('');

  useEffect(() => {
    setRows(initial);
  }, [initial]);

  const filtered = rows.filter((r) => {
    const q = filter.toLowerCase();
    const matchesText =
      !q ||
      r.project.toLowerCase().includes(q) ||
      r.repo_name.toLowerCase().includes(q);
    const matchesPhase = !phaseFilter || r.assigned_phase === phaseFilter;
    return matchesText && matchesPhase;
  });

  const dirty = rows.some((r, i) => r.assigned_phase !== initial[i]?.assigned_phase);

  const setPhase = (key: string, phase: string) => {
    setRows((prev) =>
      prev.map((r) => (r.key === key ? { ...r, assigned_phase: phase } : r)),
    );
  };

  const applySuggested = () => {
    setRows((prev) =>
      prev.map((r) => ({
        ...r,
        assigned_phase: r.suggested_phase ?? r.assigned_phase ?? defaultPhase,
      })),
    );
  };

  const handleSave = () => {
    onSave(
      rows.map((r) => ({
        project: r.project,
        repo_name: r.repo_name,
        assigned_phase: r.assigned_phase ?? defaultPhase,
      })),
    );
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
          value={phaseFilter}
          onChange={(e) => setPhaseFilter(e.target.value)}
        >
          <option value="">All phases</option>
          {sortedPhases.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button type="button" className="oai-button oai-button-secondary" onClick={applySuggested}>
          Reset to suggested
        </button>
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={!dirty || saving}
          onClick={handleSave}
        >
          {saving ? 'Saving…' : 'Save phase assignments'}
        </button>
      </div>

      <div className="discovery-table-scroll">
        <table className="discovery-table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Repository</th>
              <th>Risk</th>
              <th>Pipelines</th>
              <th>Suggested</th>
              <th>Assigned phase</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const suggested = r.suggested_phase ?? defaultPhase;
              const changed = r.assigned_phase !== suggested;
              return (
                <tr key={r.key} className={changed ? 'discovery-row-overridden' : undefined}>
                  <td>{r.project}</td>
                  <td>
                    <code>{r.repo_name}</code>
                    {r.gh_repo && r.gh_repo !== r.repo_name && (
                      <span className="discovery-gh-target"> → {r.gh_org}/{r.gh_repo}</span>
                    )}
                  </td>
                  <td>{r.total_score.toFixed(0)}</td>
                  <td>{r.pipeline_count}</td>
                  <td>
                    <span className="discovery-phase-pill discovery-phase-suggested">
                      {labelFor(suggested)}
                    </span>
                  </td>
                  <td>
                    <select
                      className="oai-input discovery-phase-select"
                      value={r.assigned_phase ?? defaultPhase}
                      onChange={(e) => setPhase(r.key, e.target.value)}
                    >
                      {sortedPhases.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="discovery-empty">No repos match the current filters.</p>
        )}
      </div>
      <p className="discovery-footnote">
        {rows.length} repo(s) · Auto-assigned phases are suggestions — adjust and save before migrating.
      </p>
    </div>
  );
}
