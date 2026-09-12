/**
 * GAP-025: render coverage for the session strip.
 *
 * Before the bootstrap and session calls resolve it renders nothing at all — deliberately,
 * so the header never flashes "Sign in" at an operator who is already signed in, nor
 * "Create admin account" on a platform that already has one.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';

import { UserSessionBar } from './UserSessionBar';

describe('UserSessionBar', () => {
  it('renders nothing until the session state is known', () => {
    const html = renderMarkup(<UserSessionBar />);

    expect(html).toBe('');
    expect(html).not.toContain('Sign in');
    expect(html).not.toContain('Log out');
  });
});
