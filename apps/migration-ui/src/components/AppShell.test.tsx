/**
 * This test provides render coverage for the console frame (register id GAP-025).
 *
 * Two behaviours live here and both are path-driven: login and onboarding render bare (no
 * header, no navigation, and critically no session gate to redirect them into a loop),
 * while every other route renders behind `AuthGate` and shows nothing until the session is
 * checked.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { navState, resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import { AppShell } from './AppShell';

afterEach(() => resetNavState());

const child = <p id="page">page body</p>;

describe('AppShell', () => {
  it.each(['/login', '/onboarding/profile'])('renders %s bare, with no console chrome', (path) => {
    navState.pathname = path;
    const html = renderMarkup(<AppShell>{child}</AppShell>);

    expect(html).toBe('<p id="page">page body</p>');
    expect(html).not.toContain('oai-header');
  });

  it('puts every other route behind the session gate', () => {
    navState.pathname = '/settings/profiles';
    const html = renderMarkup(<AppShell>{child}</AppShell>);

    expect(textOf(html)).toBe('Checking session…');
    expect(html).not.toContain('page body');
    expect(html).not.toContain('oai-footer');
  });
});
