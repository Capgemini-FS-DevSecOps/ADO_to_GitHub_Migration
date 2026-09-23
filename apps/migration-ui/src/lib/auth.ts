import { ACCEL } from './api';

/** The signed-in platform user as returned by the accelerator. */
export type AuthUser = {
  id: string;
  username: string;
  role: string;
  display_name: string;
};

/** Capability flags carried on a session; an absent flag means the capability is not granted. */
export type PlatformPermissions = {
  can_coordinate?: boolean;
  can_operate?: boolean;
  can_approve?: boolean;
  can_approve_live_execution?: boolean;
  can_manage_users?: boolean;
  can_manage_settings?: boolean;
  can_manage_models?: boolean;
};

/** The active session: who is signed in, when it expires, and what they are allowed to do. */
export type AuthSession = {
  authenticated: boolean;
  user: AuthUser;
  expires_at: string;
  permissions: PlatformPermissions;
};

/** Whether the platform still needs its first admin, and whether auth and registration are on. */
export type BootstrapStatus = {
  needs_bootstrap: boolean;
  auth_enabled: boolean;
  registration_enabled?: boolean;
  message: string;
};

const creds: RequestInit = { credentials: 'include' };

const AUTH_CHECK_TIMEOUT_MS = 30_000;

async function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), AUTH_CHECK_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

/** Ask the accelerator whether the platform needs bootstrapping; throws when the check fails. */
export async function fetchBootstrapStatus(): Promise<BootstrapStatus> {
  const r = await fetchWithTimeout(`${ACCEL}/v1/auth/bootstrap-status`, { cache: 'no-store', ...creds });
  if (!r.ok) throw new Error('Auth status unavailable');
  return r.json();
}

/** Fetch the current session, or null when the caller is not signed in. */
export async function fetchSession(): Promise<AuthSession | null> {
  const r = await fetchWithTimeout(`${ACCEL}/v1/auth/session`, { cache: 'no-store', ...creds });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error('Session check failed');
  return r.json();
}

/**
 * Create the first administrator account from the supplied username, password and display name.
 * Only succeeds while the platform is unbootstrapped; throws with the server's message otherwise.
 */
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

/**
 * Register a new platform account. Resolves with the created user and whether it still awaits
 * administrator approval; throws with the server's message when the request is rejected.
 */
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
  return r.json() as Promise<{
    pending_approval: boolean;
    message: string;
    user: AuthUser & { status?: string };
  }>;
}

function parseAuthError(text: string, fallback: string): string {
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    if (parsed.detail === 'account_pending_approval') {
      return 'Your account is pending administrator approval.';
    }
    if (parsed.detail === 'account_disabled') {
      return 'This account has been disabled. Contact a platform administrator.';
    }
    if (parsed.detail) return parsed.detail;
  } catch {
    /* ignore */
  }
  return fallback;
}

/**
 * Sign in and establish the session cookie. Throws with a readable reason on failure, including
 * the pending-approval and disabled-account cases.
 */
export async function login(body: { username: string; password: string }) {
  const r = await fetch(`${ACCEL}/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...creds,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(parseAuthError(t, 'Invalid credentials'));
  }
  return r.json();
}

/** End the current session on the server, clearing the session cookie. */
export async function logout() {
  await fetch(`${ACCEL}/v1/auth/logout`, { method: 'POST', ...creds });
}

/** Build-time flag that forces the login gate on regardless of the platform's bootstrap state. */
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
  // Users exist in the platform store — protected routes need a session even in open-dev
  // API mode, so neither REQUIRE_AUTH nor status.auth_enabled can relax this.
  return true;
}

/** Where to send an unauthenticated visitor — the bootstrap variant when no admin exists yet. */
export function platformLoginPath(status: BootstrapStatus): string {
  return status.needs_bootstrap ? '/login?bootstrap=1' : '/login';
}
