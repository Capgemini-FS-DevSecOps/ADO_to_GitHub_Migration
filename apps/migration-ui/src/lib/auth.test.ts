import { describe, expect, it } from 'vitest';
import { platformLoginPath, platformLoginRequired } from './auth';
import type { AuthSession, BootstrapStatus } from './auth';

const bootstrapping: BootstrapStatus = {
  needs_bootstrap: true,
  auth_enabled: false,
  message: 'Create admin',
};

const signedIn: AuthSession = {
  authenticated: true,
  user: { id: '1', username: 'admin', role: 'admin', display_name: 'Admin' },
  expires_at: '2099-01-01T00:00:00Z',
  permissions: {},
};

describe('platformLoginRequired', () => {
  it('allows authenticated sessions', () => {
    expect(platformLoginRequired(bootstrapping, signedIn)).toBe(false);
  });

  it('requires bootstrap when no users exist', () => {
    expect(platformLoginRequired(bootstrapping, null)).toBe(true);
  });

  it('requires sign-in when users exist but session is missing', () => {
    const status: BootstrapStatus = {
      needs_bootstrap: false,
      auth_enabled: false,
      message: 'Sign in',
    };
    expect(platformLoginRequired(status, null)).toBe(true);
  });
});

describe('platformLoginPath', () => {
  it('routes first boot to bootstrap login', () => {
    expect(platformLoginPath(bootstrapping)).toBe('/login?bootstrap=1');
  });

  it('routes returning users to sign in', () => {
    expect(
      platformLoginPath({
        needs_bootstrap: false,
        auth_enabled: true,
        message: 'Sign in',
      }),
    ).toBe('/login');
  });
});
