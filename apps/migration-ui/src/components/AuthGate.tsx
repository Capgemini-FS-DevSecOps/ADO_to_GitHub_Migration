'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { fetchBootstrapStatus, fetchSession, REQUIRE_AUTH } from '@/lib/auth';
import { fetchOnboardingStatus } from '@/lib/api';

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(!REQUIRE_AUTH);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!REQUIRE_AUTH || pathname === '/login' || pathname.startsWith('/onboarding')) {
      setReady(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const session = await fetchSession();
        if (cancelled) return;
        if (!session?.authenticated) {
          const status = await fetchBootstrapStatus();
          if (cancelled) return;
          router.replace(status.needs_bootstrap ? '/login?bootstrap=1' : '/login');
          return;
        }
        const onboarding = await fetchOnboardingStatus();
        if (cancelled) return;
        if (onboarding.needs_profile_setup && onboarding.role === 'admin') {
          if (!pathname.startsWith('/onboarding')) {
            router.replace('/onboarding/profile');
            return;
          }
        }
        if (onboarding.blocked_message && onboarding.role !== 'admin') {
          setBlockedMessage(onboarding.blocked_message);
        }
        setReady(true);
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

  return (
    <>
      {blockedMessage && (
        <div className="oai-card" style={{ marginBottom: 16, borderColor: 'var(--warning)' }}>
          <p>{blockedMessage}</p>
        </div>
      )}
      {children}
    </>
  );
}
