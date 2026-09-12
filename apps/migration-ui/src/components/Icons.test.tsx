/**
 * GAP-025: render coverage for the icon set.
 *
 * Every icon takes the same three props and the navigation tints them by active state, so
 * the contract worth pinning is that each export renders an SVG that honours `size` and
 * `color`.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';

import * as Icons from './Icons';

const ICONS = Object.entries(Icons) as [string, (p: Record<string, unknown>) => JSX.Element][];

describe('Icons', () => {
  it('exports the icons the navigation maps by name', () => {
    const names = ICONS.map(([name]) => name);
    for (const required of [
      'ChartBarIcon',
      'BoltIcon',
      'CloudIcon',
      'GithubIcon',
      'SearchIcon',
      'PlusIcon',
      'BotIcon',
      'ArrowRightLeftIcon',
      'ActivityIcon',
      'SettingsIcon',
    ]) {
      expect(names).toContain(required);
    }
  });

  it.each(ICONS)('%s renders an SVG honouring size and color', (_name, Icon) => {
    const html = renderMarkup(<Icon size={32} color="#35B8FF" className="tab-icon" />);

    expect(html).toContain('<svg');
    expect(html).toContain('width="32"');
    expect(html).toContain('#35B8FF');
    expect(html).toContain('tab-icon');
  });
});
