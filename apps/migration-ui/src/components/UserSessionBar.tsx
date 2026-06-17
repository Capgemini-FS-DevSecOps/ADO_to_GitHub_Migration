'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  fetchBootstrapStatus,
  fetchSession,
  logout,
  type AuthUser,
} from '@/lib/auth';

export function UserSessionBar() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [needsBootstrap, setNeedsBootstrap] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await fetchBootstrapStatus();
        if (!cancelled) setNeedsBootstrap(status.needs_bootstrap);
        const session = await fetchSession();
        if (!cancelled && session?.authenticated) setUser(session.user);
      } catch {
        /* API down — show sign-in link anyway */
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!loaded) return null;

  const handleLogout = async () => {
    await logout();
    window.location.href = '/login';
  };

  return (
    <div className="user-session-bar">
      {user ? (
        <>
          <span className="user-session-label">
            {user.display_name || user.username}
            <span className="user-session-role">{user.role}</span>
          </span>
          <button type="button" className="oai-button oai-button-ghost user-session-logout" onClick={handleLogout}>
            Log out
          </button>
        </>
      ) : (
        <Link href={needsBootstrap ? '/login?bootstrap=1' : '/login'} className="user-session-login">
          {needsBootstrap ? 'Create admin account' : 'Sign in'}
        </Link>
      )}
    </div>
  );
}
