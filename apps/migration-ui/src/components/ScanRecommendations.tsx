'use client';

import type { MigrationScanResult, PhaseDefinition } from '@/lib/types';

export function ScanRecommendations({
  scan,
  phases,
  compact = false,
}: {
  scan: MigrationScanResult;
  phases?: PhaseDefinition[];
  compact?: boolean;
}) {
  const ordered =
    phases ??
    Object.keys(scan.recommendations).map((id, order) => ({
      id,
      name: scan.recommendations[id]?.phase_name ?? id,
      order,
      risk_max: scan.recommendations[id]?.risk_band_max ?? 0,
      repo_cap: 9999,
    }));

  const sorted = [...ordered].sort((a, b) => a.order - b.order);

  return (
    <div className="scan-recommendations">
      <div className="scan-summary-row">
        <span>{scan.projects_scanned} project(s)</span>
        <span>{scan.repos_scanned} repo(s) scanned</span>
        {scan.scanned_at && (
          <span className="scan-timestamp">
            {new Date(scan.scanned_at).toLocaleString()}
          </span>
        )}
      </div>
      <div className={`scan-phase-grid${compact ? ' scan-phase-grid-compact' : ''}`}>
        {sorted.map((phaseDef) => {
          const bucket = scan.recommendations[phaseDef.id];
          if (!bucket) {
            return (
              <div key={phaseDef.id} className="scan-phase-card scan-phase-card-empty">
                <div className="scan-phase-header">
                  <span className="scan-phase-name">{phaseDef.name}</span>
                  <span className="scan-phase-count">0 repos</span>
                </div>
              </div>
            );
          }
          return (
            <div key={phaseDef.id} className="scan-phase-card">
              <div className="scan-phase-header">
                <span className="scan-phase-name">{phaseDef.name}</span>
                <span className="scan-phase-count">{bucket.repo_count} repos</span>
              </div>
              {(bucket.risk_max > 0 || bucket.risk_min > 0) && (
                <p className="scan-phase-risk">
                  Scores {bucket.risk_min}–{bucket.risk_max}
                  {bucket.risk_band_max ? ` (band ≤ ${bucket.risk_band_max})` : ''}
                </p>
              )}
              <p className="scan-phase-rationale">{bucket.rationale}</p>
              {!compact && (bucket.repos?.length ?? 0) > 0 && (
                <ul className="scan-repo-list">
                  {(bucket.repos ?? []).slice(0, 8).map((r) => (
                    <li key={`${r.project}/${r.repo_name}`}>
                      {r.project}/{r.repo_name}
                      <span className="scan-repo-score">{r.total_score}</span>
                    </li>
                  ))}
                  {(bucket.repos?.length ?? 0) > 8 && (
                    <li className="scan-repo-more">+{(bucket.repos?.length ?? 0) - 8} more</li>
                  )}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
