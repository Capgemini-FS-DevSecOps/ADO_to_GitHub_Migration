/**
 * This test provides render coverage for the discovered-repository table (register id GAP-025).
 *
 * Paging is client-side and silent: past 25 rows the table shows a slice, and nothing in
 * the markup tells the operator so except the pager. A regression there hides repositories
 * from a migration plan without any error.
 */
import { describe, it, expect } from 'vitest';

import type { DiscoveryRepoItem } from '@/lib/types';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { DiscoveryTable } from './DiscoveryTable';

const repo = (n: number): DiscoveryRepoItem => ({
  project: 'Payments',
  repo_name: `svc-${n}`,
  total_score: n,
  pipeline_count: 2,
});

describe('DiscoveryTable', () => {
  it('lists each repository with its risk score and pipeline count', () => {
    const html = renderMarkup(<DiscoveryTable repos={[repo(1), repo(2)]} />);
    const text = textOf(html);

    expect(text).toContain('svc-1');
    expect(text).toContain('svc-2');
    expect(html.match(/<tr>/g)).toHaveLength(3); // header + 2 rows
    expect(text).toContain('2 repo(s)');
  });

  it('names the GitHub target only when it differs from the ADO repo name', () => {
    const renamed = { ...repo(1), gh_org: 'contoso', gh_repo: 'payments-svc-1' };
    expect(textOf(renderMarkup(<DiscoveryTable repos={[renamed]} />))).toContain(
      'contoso/payments-svc-1',
    );

    const same = { ...repo(1), gh_org: 'contoso', gh_repo: 'svc-1' };
    expect(renderMarkup(<DiscoveryTable repos={[same]} />)).not.toContain('discovery-gh-target');
  });

  it('says so when nothing matches instead of showing an empty table', () => {
    const text = textOf(renderMarkup(<DiscoveryTable repos={[]} />));
    expect(text).toContain('No repos match the current filters.');
    expect(text).toContain('0 repo(s)');
  });

  it('pages at 25 rows and starts on page 1 with Previous disabled', () => {
    const twentyFive = Array.from({ length: 25 }, (_, i) => repo(i));
    expect(renderMarkup(<DiscoveryTable repos={twentyFive} />)).not.toContain(
      'history-pagination',
    );

    const twentySix = Array.from({ length: 26 }, (_, i) => repo(i));
    const html = renderMarkup(<DiscoveryTable repos={twentySix} />);
    expect(textOf(html)).toContain('Page 1 of 2');
    expect(html.match(/<tr>/g)).toHaveLength(26); // header + first page of 25
    expect(html).toMatch(/disabled[^>]*>Previous|Previous/);
  });

  it('offers one filter option per distinct project', () => {
    const html = renderMarkup(
      <DiscoveryTable repos={[repo(1), { ...repo(2), project: 'Billing' }]} />,
    );
    expect(html.match(/<option/g)).toHaveLength(3); // "All projects" + Billing + Payments
  });
});
