import { ACCEL } from './api';

export type AuthUser = {
  id: string;
  username: string;
  role: string;
  display_name: string;
};

export type PlatformPermissions = {
  can_coordinate?: boolean;
  can_operate?: boolean;
  can_approve?: boolean;
  can_approve_live_execution?: boolean;
  can_manage_users?: boolean;
  can_manage_settings?: boolean;
  can_manage_models?: boolean;
};

export type AuthSession = {
  authenticated: boolean;
  user: AuthUser;
  expires_at: string;
  permissions: PlatformPermissions;
};

export type BootstrapStatus = {
  needs_bootstrap: boolean;
  auth_enabled: boolean;
  registration_enabled?: boolean;
  message: string;
};

const creds: RequestInit = { credentials: 'include' };

export async function fetchBootstrapStatus(): Promise<BootstrapStatus> {
  const r = await fetch(`${ACCEL}/v1/auth/bootstrap-status`, { cache: 'no-store', ...creds });
  if (!r.ok) throw new Error('Auth status unavailable');
  return r.json();
}

export async function fetchSession(): Promise<AuthSession | null> {
  const r = await fetch(`${ACCEL}/v1/auth/session`, { cache: 'no-store', ...creds });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error('Session check failed');
  return r.json();
}

export async function bootstrapAdmin(body: {
  username: string;
  password: string;
  display_name: string;
}) {
  const r = await fetch(`${ACCEL}/v1/auth/bootstrap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...creds,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || 'Bootstrap failed');
  }
  return r.json();
}

export async function register(body: {
  username: string;
  password: string;
  display_name: string;
}) {
  const r = await fetch(`${ACCEL}/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...creds,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || 'Registration failed');
  }
  return r.json();
}

export async function login(body: { username: string; password: string }) {
  const r = await fetch(`${ACCEL}/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...creds,
  });
  if (!r.ok) throw new Error('Invalid credentials');
  return r.json();
}

export async function logout() {
  await fetch(`${ACCEL}/v1/auth/logout`, { method: 'POST', ...creds });
}

export const REQUIRE_AUTH =
  process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true' ||
  process.env.NEXT_PUBLIC_REQUIRE_AUTH === '1';

/** Whether the UI should block unauthenticated access (runtime bootstrap + build flag). */
export function platformLoginRequired(
  status: BootstrapStatus,
  session: AuthSession | null,
): boolean {
  if (session?.authenticated) return false;
  if (status.needs_bootstrap) return true;
  if (REQUIRE_AUTH || status.auth_enabled) return true;
  // Users exist in the platform store — protected routes need a session even in open-dev API mode.
  return true;
}

export function platformLoginPath(status: BootstrapStatus): string {
  return status.needs_bootstrap ? '/login?bootstrap=1' : '/login';
}
