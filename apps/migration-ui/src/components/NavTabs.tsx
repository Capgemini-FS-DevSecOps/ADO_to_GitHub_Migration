'use client';

import { usePathname } from 'next/navigation';
import Link from 'next/link';
import {
  ActivityIcon,
  ArrowRightLeftIcon,
  BotIcon,
  ChartBarIcon,
  CheckCircleIcon,
  ClipboardListIcon,
  HistoryIcon,
  SearchIcon,
  SettingsIcon,
  WorkflowIcon,
} from '@/components/Icons';

const NAV_ITEMS = [
  { href: '/', label: 'Dashboard', icon: ChartBarIcon },
  { href: '/agent', label: 'Agent', icon: BotIcon },
  { href: '/assignments', label: 'Assignments', icon: ClipboardListIcon },
  { href: '/history', label: 'History', icon: HistoryIcon },
  { href: '/migrate', label: 'Migrate', icon: ArrowRightLeftIcon },
  { href: '/runs', label: 'Monitor', icon: ActivityIcon },
  { href: '/discovery', label: 'Discovery', icon: SearchIcon },
  { href: '/readiness', label: 'Readiness', icon: CheckCircleIcon },
  { href: '/workflows', label: 'Workflows', icon: WorkflowIcon },
  { href: '/validation', label: 'Validation', icon: CheckCircleIcon },
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
