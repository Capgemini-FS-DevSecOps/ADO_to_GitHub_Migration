'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { fetchSettings, runValidation } from '@/lib/api';

export default function ValidationPage() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const adv = settings?.advanced;

  const [configPath, setConfigPath] = useState('');
  const [dbPath, setDbPath] = useState('');
  const [inputPath, setInputPath] = useState('');

  const validateMut = useMutation({
    mutationFn: () =>
      runValidation({
        config_path: configPath || adv?.config_path || 'migration.yaml',
        db_path: dbPath || adv?.db_path || 'migration_state.db',
        input_path: inputPath || undefined,
      }),
  });

  const result = validateMut.data;

  return (
    <div>
      <h1 className="oai-page-title">Validation</h1>
      <p>
        Commit-level SHA verification — compares source ADO repos against GitHub targets after migration.
      </p>

      <div className="content-layout">
        <div className="setup-sidebar">
          <div className="oai-card">
            <h2 className="oai-subsection-title">Validate configuration</h2>
            <div className="form-grid">
              <div className="form-row">
                <label>Config path</label>
                <input
                  className="oai-input"
                  placeholder={adv?.config_path || 'migration.yaml'}
                  value={configPath}
                  onChange={(e) => setConfigPath(e.target.value)}
                />
              </div>
              <div className="form-row">
                <label>State DB path</label>
                <input
                  className="oai-input"
                  placeholder={adv?.db_path || 'migration_state.db'}
                  value={dbPath}
                  onChange={(e) => setDbPath(e.target.value)}
                />
              </div>
              <div className="form-row">
                <label>Input path (optional)</label>
                <input
                  className="oai-input"
                  placeholder="repos.txt or wave input"
                  value={inputPath}
                  onChange={(e) => setInputPath(e.target.value)}
                />
              </div>
            </div>
            <button
              type="button"
              className="oai-button oai-button-primary"
              style={{ marginTop: 16, width: '100%' }}
              disabled={validateMut.isPending}
              onClick={() => validateMut.mutate()}
            >
              {validateMut.isPending ? 'Validating…' : 'Run validation'}
            </button>
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
                      {result.results.map((row, i) => (
                        <tr
                          key={`${row.project}-${row.repo}-${i}`}
                          className={row.overall === 'PASS' ? 'validation-row-pass' : 'validation-row-fail'}
                        >
                          <td>{String(row.project ?? '')}</td>
                          <td><code>{String(row.repo ?? row.repo_name ?? '')}</code></td>
                          <td>{String(row.overall ?? row.status ?? '')}</td>
                          <td>{String(row.message ?? row.detail ?? '')}</td>
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
                SHAs between ADO and GitHub for each migrated repository.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
