/**
 * GAP-025: the console home renders its loading state until the dashboard data arrives.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import HomePage from './page';

afterEach(() => resetNavState());

describe('/', () => {
  it('announces that the dashboard is loading and shows no run data yet', () => {
    const text = textOf(renderMarkup(<HomePage />));

    expect(text).toContain('Loading dashboard');
    expect(text).not.toContain('Start migration');
  });
});
