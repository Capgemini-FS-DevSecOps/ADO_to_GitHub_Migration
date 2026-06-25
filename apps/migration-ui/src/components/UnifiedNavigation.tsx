'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import {
  ActivityIcon,
  ArrowRightLeftIcon,
  BotIcon,
  ChartBarIcon,
  SearchIcon,
  SettingsIcon,
} from '@/components/Icons';
import { UNIFIED_TABS } from '@/lib/navigationState';

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; color?: string }>> = {
  search: SearchIcon,
  'arrow-right-left': ArrowRightLeftIcon,
  bot: BotIcon,
  settings: SettingsIcon,
  activity: ActivityIcon,
  chart: ChartBarIcon,
};

export function UnifiedNavigation() {
  const pathname = usePathname();

  return (
    <nav className="oai-tabs" aria-label="Unified migration navigation">
      {UNIFIED_TABS.map((tab) => {
        const Icon = ICON_MAP[tab.icon ?? ''] ?? SearchIcon;
        const active =
          tab.href === '/settings'
            ? pathname === '/settings' ||
              (pathname.startsWith('/settings/') &&
                !pathname.startsWith('/settings/discovery') &&
                !pathname.startsWith('/settings/migrate') &&
                !pathname.startsWith('/settings/agent'))
            : tab.href === '/'
              ? pathname === '/'
              : pathname === tab.href || pathname.startsWith(tab.href + '/');

        return (
          <Link
            key={tab.id}
            href={tab.href}
            className={`oai-tab${active ? ' oai-tab-active' : ''}`}
            title={tab.description}
          >
            <span className="tab-icon" aria-hidden>
              <Icon size={16} color={active ? '#35B8FF' : '#888'} />
            </span>
            <span className="tab-ticker">{tab.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function UnifiedSubNavigation({
  tabs,
}: {
  tabs: { id: string; label: string; href: string }[];
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentTab = searchParams.get('tab');

  return (
    <nav className="oai-tabs" aria-label="Sub-navigation" style={{ marginBottom: 16 }}>
      {tabs.map((tab) => {
        const isDefault = !tab.href.includes('?tab=');
        const tabParam = tab.href.match(/\?tab=(\w+)/)?.[1];
        const active = isDefault
          ? !currentTab && pathname === tab.href.replace('?tab=', '').split('?')[0]
          : currentTab === tabParam;

        return (
          <Link
            key={tab.id}
            href={tab.href}
            className={`oai-tab${active ? ' oai-tab-active' : ''}`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
