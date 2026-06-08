'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const TOP_ITEMS = [
  { href: '/settings/profiles', label: 'Migration Profiles' },
  { href: '/settings/advanced', label: 'Advanced' },
];

export function SettingsNav() {
  const pathname = usePathname();

  return (
    <div className="oai-tabs-wrapper" style={{ marginBottom: '1.5rem' }}>
      <div className="oai-tabs">
        {TOP_ITEMS.map(({ href, label }) => {
          const active =
            pathname === href ||
            (href === '/settings/profiles' && pathname.startsWith('/settings/profiles'));
          return (
            <Link key={href} href={href} className={`oai-tab${active ? ' oai-tab-active' : ''}`}>
              <span className="tab-ticker">{label}</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

export function ProfileNav({ profileId, profileName }: { profileId: string; profileName: string }) {
  const pathname = usePathname();
  const base = `/settings/profiles/${profileId}`;

  const items = [
    { href: `${base}/source`, label: 'Source (ADO)' },
    { href: `${base}/tokens`, label: 'Target (GitHub Tokens)' },
  ];

  return (
    <div className="profile-nav">
      <Link href="/settings/profiles" className="profile-back-link">
        ← All migration profiles
      </Link>
      <h2 className="oai-subsection-title profile-nav-title">{profileName}</h2>
      <div className="oai-tabs-wrapper">
        <div className="oai-tabs">
          {items.map(({ href, label }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link key={href} href={href} className={`oai-tab${active ? ' oai-tab-active' : ''}`}>
                <span className="tab-ticker">{label}</span>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
