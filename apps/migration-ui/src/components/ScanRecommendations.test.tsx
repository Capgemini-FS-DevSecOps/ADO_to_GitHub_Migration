/**
 * This test provides render coverage for the phase recommendations panel (register id GAP-025).
 *
 * This is where an operator reads which repositories a scan wants in which phase, so the
 * ordering (by `order`, not by object key), the per-phase counts, and the truncation of
 * long repo lists all carry meaning.
 */
import { describe, it, expect } from 'vitest';

import type { MigrationScanResult, PhaseDefinition } from '@/lib/types';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { ScanRecommendations } from './ScanRecommendations';

const scan: MigrationScanResult = {
  scanned_at: '',
  projects_scanned: 2,
  repos_scanned: 11,
  total_repos: 11,
  gh_org: 'contoso',
  recommendations: {
    poc: {
      phase: 'poc',
      phase_name: 'POC',
      repo_count: 2,
      risk_min: 1,
      risk_max: 3,
      risk_band_max: 3,
      rationale: 'Low risk, no pipelines',
      repos: [
        { project: 'Payments', repo_name: 'svc-a', total_score: 1 },
        { project: 'Payments', repo_name: 'svc-b', total_score: 3 },
      ],
    },
    wave1: {
      phase: 'wave1',
      phase_name: 'Wave 1',
      repo_count: 9,
      risk_min: 4,
      risk_max: 9,
      rationale: 'Everything else',
      repos: Array.from({ length: 9 }, (_, i) => ({
        project: 'Billing',
        repo_name: `svc-${i}`,
        total_score: 5,
      })),
    },
  },
};

const phases: PhaseDefinition[] = [
  { id: 'wave1', name: 'Wave 1', risk_max: 9, repo_cap: 50, order: 2 },
  { id: 'poc', name: 'POC', risk_max: 3, repo_cap: 5, order: 1 },
  { id: 'wave2', name: 'Wave 2', risk_max: 10, repo_cap: 50, order: 3 },
];

describe('ScanRecommendations', () => {
  it('summarises the scan and lists each phase with its repo count and rationale', () => {
    const text = textOf(renderMarkup(<ScanRecommendations scan={scan} />));

    expect(text).toContain('2 project(s)');
    expect(text).toContain('11 repo(s) scanned');
    expect(text).toContain('POC 2 repos');
    expect(text).toContain('Low risk, no pipelines');
    expect(text).toContain('Scores 1–3 (band ≤ 3)');
  });

  it('orders the cards by phase order, not by recommendation key', () => {
    const text = textOf(renderMarkup(<ScanRecommendations scan={scan} phases={phases} />));
    expect(text.indexOf('POC')).toBeLessThan(text.indexOf('Wave 1'));
  });

  it('shows a phase with no recommendation as an empty card', () => {
    const html = renderMarkup(<ScanRecommendations scan={scan} phases={phases} />);

    expect(html).toContain('scan-phase-card-empty');
    expect(textOf(html)).toContain('Wave 2 0 repos');
  });

  it('lists at most eight repos per phase and counts the rest', () => {
    const text = textOf(renderMarkup(<ScanRecommendations scan={scan} />));

    expect(text).toContain('Billing/svc-7');
    expect(text).not.toContain('Billing/svc-8');
    expect(text).toContain('+1 more');
  });
});
