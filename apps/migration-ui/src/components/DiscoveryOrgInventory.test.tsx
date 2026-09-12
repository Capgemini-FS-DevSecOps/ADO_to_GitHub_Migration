/**
 * GAP-025: render coverage for the org-wide ADO inventory panel.
 *
 * "Pipelines indexed" is read from three different server fields in priority order — the
 * kind of fallback chain that silently reports 0 when one of them is renamed.
 */
import { describe, it, expect } from 'vitest';

import type { ScanProjectDetail } from '@/lib/types';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { DiscoveryOrgInventory } from './DiscoveryOrgInventory';

const projectDetails: ScanProjectDetail[] = [
  { project: 'Payments', project_id: 'p1', repo_count: 3, pipeline_count: 7 },
];

describe('DiscoveryOrgInventory', () => {
  it('renders nothing when a scan returned neither inventory nor project detail', () => {
    expect(renderMarkup(<DiscoveryOrgInventory />)).toBe('');
    expect(renderMarkup(<DiscoveryOrgInventory projectDetails={[]} />)).toBe('');
  });

  it('shows every org-wide count it was given', () => {
    const text = textOf(
      renderMarkup(
        <DiscoveryOrgInventory
          orgInventory={{
            total_service_connections: 4,
            total_variable_groups: 5,
            total_environments: 6,
            total_artifact_feeds: 7,
            total_work_items: 8,
            total_teams: 9,
            total_iterations: 10,
            total_test_plans: 11,
          }}
        />,
      ),
    );

    expect(text).toContain('4 Service connections');
    expect(text).toContain('5 Variable groups');
    expect(text).toContain('6 Environments');
    expect(text).toContain('7 Artifact feeds');
    expect(text).toContain('8 Work items');
    expect(text).toContain('11 Test plans');
  });

  it('counts missing inventory fields as zero rather than blank', () => {
    const text = textOf(renderMarkup(<DiscoveryOrgInventory orgInventory={{}} />));
    expect(text).toContain('0 Service connections');
    expect(text).toContain('0 Pipelines indexed');
  });

  it('prefers the explicit pipeline count over either inventory field', () => {
    const inventory = { pipeline_inventory_count: 20, total_pipelines_indexed: 30 };

    expect(
      textOf(
        renderMarkup(
          <DiscoveryOrgInventory orgInventory={inventory} pipelineInventoryCount={10} />,
        ),
      ),
    ).toContain('10 Pipelines indexed');

    expect(
      textOf(renderMarkup(<DiscoveryOrgInventory orgInventory={inventory} />)),
    ).toContain('20 Pipelines indexed');

    expect(
      textOf(
        renderMarkup(<DiscoveryOrgInventory orgInventory={{ total_pipelines_indexed: 30 }} />),
      ),
    ).toContain('30 Pipelines indexed');
  });

  it('renders the per-project breakdown when only project detail is available', () => {
    const text = textOf(renderMarkup(<DiscoveryOrgInventory projectDetails={projectDetails} />));
    expect(text).toContain('Payments');
  });
});
