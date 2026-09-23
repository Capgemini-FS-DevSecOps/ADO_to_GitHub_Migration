/**
 * This test provides render coverage for the settings tabs (register id GAP-025).
 *
 * Which tab reads as active is computed from the path with a prefix rule, so a nested
 * profile route has to keep "Migration Profiles" lit rather than lighting nothing.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { navState, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import { ProfileNav, SettingsNav } from './SettingsNav';

afterEach(() => resetNavState());

const activeLabels = (html: string): string[] =>
  Array.from(html.matchAll(/oai-tab oai-tab-active[\s\S]*?>([^<]+)</g)).map((m) => m[1].trim());

describe('SettingsNav', () => {
  it('links both top-level settings tabs', () => {
    navState.pathname = '/settings/profiles';
    const text = textOf(renderMarkup(<SettingsNav />));

    expect(text).toContain('Migration Profiles');
    expect(text).toContain('Advanced');
  });

  it('keeps the profiles tab active on a nested profile route', () => {
    navState.pathname = '/settings/profiles/p1/tokens';
    expect(activeLabels(renderMarkup(<SettingsNav />))).toEqual(['Migration Profiles']);
  });

  it('activates the advanced tab on its own route only', () => {
    navState.pathname = '/settings/advanced';
    expect(activeLabels(renderMarkup(<SettingsNav />))).toEqual(['Advanced']);
  });
});

describe('ProfileNav', () => {
  it('names the profile and links back to the profile list', () => {
    navState.pathname = '/settings/profiles/p1/source';
    const html = renderMarkup(<ProfileNav profileId="p1" profileName="Contoso production" />);

    expect(textOf(html)).toContain('Contoso production');
    expect(html).toContain('href="/settings/profiles"');
    expect(html).toContain('href="/settings/profiles/p1/tokens"');
  });

  it('activates the tab matching the current profile sub-route', () => {
    navState.pathname = '/settings/profiles/p1/tokens/t1';
    const html = renderMarkup(<ProfileNav profileId="p1" profileName="Contoso" />);

    expect(activeLabels(html)).toEqual(['Target (GitHub Tokens)']);
  });
});
