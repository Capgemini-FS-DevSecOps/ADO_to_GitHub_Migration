/**
 * GAP-025: render coverage for the primary tab bar.
 *
 * The Settings tab is active for anything under `/settings` *except* the three routes that
 * have tabs of their own — a four-clause condition with no test behind it, where getting it
 * wrong lights two tabs at once or none.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { UNIFIED_TABS } from '@/lib/types/navigation';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { navState, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import { UnifiedNavigation, UnifiedSubNavigation } from './UnifiedNavigation';

afterEach(() => resetNavState());

const activeLabels = (html: string): string[] =>
  Array.from(html.matchAll(/oai-tab oai-tab-active[\s\S]*?tab-ticker">([^<]+)</g)).map((m) =>
    m[1].trim(),
  );

describe('UnifiedNavigation', () => {
  it('renders every configured tab', () => {
    const text = textOf(renderMarkup(<UnifiedNavigation />));
    for (const tab of UNIFIED_TABS) expect(text).toContain(tab.label);
  });

  it.each([
    ['/', 'Dashboard'],
    ['/settings/agent', 'Agent'],
    ['/settings/migrate', 'Migrate'],
    ['/settings/discovery', 'Discovery'],
    ['/settings/profiles', 'Settings'],
    ['/settings', 'Settings'],
  ])('marks exactly one tab active on %s', (path, label) => {
    navState.pathname = path;
    expect(activeLabels(renderMarkup(<UnifiedNavigation />))).toEqual([label]);
  });

  it('does not light the dashboard tab on a nested route', () => {
    navState.pathname = '/settings/users';
    expect(activeLabels(renderMarkup(<UnifiedNavigation />))).not.toContain('Dashboard');
  });
});

describe('UnifiedSubNavigation', () => {
  const tabs = [
    { id: 'overview', label: 'Overview', href: '/settings/discovery' },
    { id: 'readiness', label: 'Pipeline Readiness', href: '/settings/discovery?tab=readiness' },
  ];

  it('activates the default tab when no tab query parameter is set', () => {
    navState.pathname = '/settings/discovery';
    expect(activeLabels(renderMarkup(<UnifiedSubNavigation tabs={tabs} />))).toEqual([]);
    expect(renderMarkup(<UnifiedSubNavigation tabs={tabs} />)).toContain('oai-tab-active');
  });

  it('activates the tab named by the tab query parameter', () => {
    navState.pathname = '/settings/discovery';
    navState.search = 'tab=readiness';
    const html = renderMarkup(<UnifiedSubNavigation tabs={tabs} />);

    expect(html).toContain('oai-tab oai-tab-active');
    expect(html).toMatch(/oai-tab oai-tab-active[^>]*>Pipeline Readiness/);
  });
});
