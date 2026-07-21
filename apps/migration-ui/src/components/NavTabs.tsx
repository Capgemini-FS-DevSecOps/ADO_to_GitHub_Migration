'use client';

/**
 * @deprecated NavTabs is deprecated. Use UnifiedNavigation instead.
 * Scheduled for removal after all routes are verified under /settings.
 */

import { usePathname } from 'next/navigation';
import Link from 'next/link';
import {
  ActivityIcon,
  ArrowRightLeftIcon,
  BotIcon,
  ChartBarIcon,
  HistoryIcon,
  SearchIcon,
  SettingsIcon,
} from '@/components/Icons';

const NAV_ITEMS = [
  { href: '/', label: 'Dashboard', icon: ChartBarIcon },
  { href: '/settings/agent', label: 'Agent', icon: BotIcon },
  { href: '/history', label: 'History', icon: HistoryIcon },
  { href: '/settings/migrate', label: 'Migrate', icon: ArrowRightLeftIcon },
  { href: '/runs', label: 'Monitor', icon: ActivityIcon },
  { href: '/settings/discovery', label: 'Discovery', icon: SearchIcon },
  { href: '/settings', label: 'Settings', icon: SettingsIcon },
] as const;

export function NavTabs() {
  const pathname = usePathname();

  return (
    <div className="oai-tabs-wrapper">
      <div className="oai-tabs-container">
        <nav className="oai-tabs" aria-label="Migration console">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`oai-tab${active ? ' oai-tab-active' : ''}`}
              >
                <span className="tab-icon" aria-hidden>
                  <Icon size={16} color={active ? '#35B8FF' : '#888'} />
                </span>
                <span className="tab-ticker">{label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
