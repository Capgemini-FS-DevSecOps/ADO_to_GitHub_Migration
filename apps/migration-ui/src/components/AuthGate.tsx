'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { fetchBootstrapStatus, fetchSession, REQUIRE_AUTH } from '@/lib/auth';

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(!REQUIRE_AUTH);

  useEffect(() => {
    if (!REQUIRE_AUTH || pathname === '/login') {
      setReady(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const session = await fetchSession();
        if (cancelled) return;
        if (session?.authenticated) {
          setReady(true);
          return;
        }
        const status = await fetchBootstrapStatus();
        if (cancelled) return;
        router.replace(status.needs_bootstrap ? '/login?bootstrap=1' : '/login');
      } catch {
        if (!cancelled) router.replace('/login?error=api');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (!ready) {
    return (
      <div className="oai-loading" style={{ minHeight: '40vh' }}>
        <div className="oai-spinner" />
        <p>Checking session…</p>
      </div>
    );
  }
  return <>{children}</>;
}
