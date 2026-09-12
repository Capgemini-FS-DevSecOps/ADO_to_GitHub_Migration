/**
 * GAP-025: render coverage for the session gate.
 *
 * The gate's whole job is that nothing behind it renders until a session has been checked.
 * A server render is exactly the pre-check state (no effects have run), so this pins the
 * property that matters: page content is withheld, and the operator is told why.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import { AuthGate } from './AuthGate';

afterEach(() => resetNavState());

describe('AuthGate', () => {
  it('withholds its children until the session check has run', () => {
    const html = renderMarkup(
      <AuthGate>
        <p>migration profiles</p>
      </AuthGate>,
    );

    expect(textOf(html)).toBe('Checking session…');
    expect(html).not.toContain('migration profiles');
    expect(html).toContain('oai-spinner');
  });
});
