'use client';

import { usePathname } from 'next/navigation';
import Link from 'next/link';

const NAV_ITEMS = [
  { href: '/', label: 'Dashboard' },
  { href: '/agent', label: 'Agent' },
  { href: '/migrate', label: 'Migrate' },
  { href: '/runs', label: 'Monitor' },
  { href: '/discovery', label: 'Discovery' },
  { href: '/readiness', label: 'Readiness' },
  { href: '/workflows', label: 'Workflows' },
  { href: '/validation', label: 'Validation' },
  { href: '/settings', label: 'Settings' },
];

export function NavTabs() {
  const pathname = usePathname();

  return (
    <div className="oai-tabs-wrapper">
      <div className="oai-tabs-container">
        <nav className="oai-tabs" aria-label="Migration console">
          {NAV_ITEMS.map(({ href, label }) => {
            const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`oai-tab${active ? ' oai-tab-active' : ''}`}
              >
                <span className="tab-ticker">{label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
