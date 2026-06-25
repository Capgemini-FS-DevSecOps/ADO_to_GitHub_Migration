/** Navigation state management for the unified migration UI (feature 008).

Provides helpers for determining active tab, sub-tab, and navigation state
from the current URL pathname and search params.
*/
'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import { useMemo } from 'react';
import type { UnifiedTabId, DiscoverySubTab, MigrateSubTab } from './types/navigation';
import { UNIFIED_TABS, DISCOVERY_SUB_TABS, MIGRATE_SUB_TABS } from './types/navigation';

export function useActiveTab(): UnifiedTabId {
  const pathname = usePathname();
  return useMemo(() => {
    for (const tab of UNIFIED_TABS) {
      if (pathname.startsWith(tab.href)) {
        return tab.id;
      }
    }
    return 'discovery';
  }, [pathname]);
}

export function useDiscoverySubTab(): DiscoverySubTab['id'] {
  const searchParams = useSearchParams();
  const tab = searchParams.get('tab');
  if (tab === 'readiness') return 'readiness';
  if (tab === 'workflows') return 'workflows';
  if (tab === 'validation') return 'validation';
  return 'overview';
}

export function useMigrateSubTab(): MigrateSubTab['id'] {
  return 'migration';
}

export function getTabById(id: UnifiedTabId) {
  return UNIFIED_TABS.find((t) => t.id === id);
}

export { UNIFIED_TABS, DISCOVERY_SUB_TABS, MIGRATE_SUB_TABS };
