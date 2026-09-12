/**
 * GAP-025: the login route wraps the client form in a Suspense boundary because the form
 * reads search params. The fallback has to be a boot placeholder, never a blank screen.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import LoginPage from './page';

afterEach(() => resetNavState());

describe('/login', () => {
  it('renders the sign-in card inside a boot-loader fallback', () => {
    const html = renderMarkup(<LoginPage />);
    const text = textOf(html);

    expect(text).toContain('Starting console…');
    expect(text).toContain('Sign in');
    expect(html).toContain('aria-live="polite"');
  });
});
