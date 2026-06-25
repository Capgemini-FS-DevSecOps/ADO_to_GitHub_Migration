'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { fetchSession } from '@/lib/auth';
import { operatorSettingsHint, visibleSettingsTabs } from '@/lib/permissions';

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const perms = session?.permissions;
  const operatorHint = operatorSettingsHint(perms);
  const tabs = visibleSettingsTabs(perms);

  const isSettingsRoot = pathname === '/settings' || (
    pathname.startsWith('/settings/') &&
    !pathname.startsWith('/settings/discovery') &&
    !pathname.startsWith('/settings/migrate') &&
    !pathname.startsWith('/settings/agent')
  );

  return (
    <div>
      {isSettingsRoot && (
        <>
          <h1 className="oai-page-title">Settings</h1>
          {operatorHint && (
            <p className="form-hint" style={{ marginBottom: 12 }}>
              {operatorHint}
            </p>
          )}
          <nav className="oai-tabs" style={{ marginBottom: 16 }}>
            {tabs.map((t) => (
              <Link
                key={t.href}
                href={t.href}
                className={`oai-tab${pathname.startsWith(t.href) ? ' oai-tab-active' : ''}`}
                title={t.hint}
              >
                {t.label}
              </Link>
            ))}
          </nav>
        </>
      )}
      {children}
    </div>
  );
}
