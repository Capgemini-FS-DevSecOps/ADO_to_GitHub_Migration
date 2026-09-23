/**
 * The discovery tab is the entry point for a scan (register id GAP-025). With no active profile it must
 * say so and point at profile setup rather than offering a scan that cannot run.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import DiscoveryPage from './page';

afterEach(() => resetNavState());

describe('/settings/discovery', () => {
  it('renders its sub-tabs and blocks scanning without an active profile', () => {
    const text = textOf(renderMarkup(<DiscoveryPage />));

    expect(text).toContain('Overview');
    expect(text).toContain('Pipeline Readiness');
    expect(text).toContain('Discovery Overview');
    expect(text).toContain('No active migration profile');
  });
});
