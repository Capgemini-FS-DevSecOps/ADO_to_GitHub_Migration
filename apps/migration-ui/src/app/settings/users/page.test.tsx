/**
 * Platform user management creates accounts and resets passwords, so it is
 * admin-only (register id GAP-025). A render with no session must show the denial and no user list or password
 * field.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import PlatformUsersPage from './page';

describe('/settings/users', () => {
  it('renders the admin-only notice and no user management controls', () => {
    const html = renderMarkup(<PlatformUsersPage />);

    expect(textOf(html)).toBe(
      'Admin only — user management is restricted to platform administrators.',
    );
    expect(html).not.toContain('Platform users');
    expect(html).not.toContain('type="password"');
  });
});
