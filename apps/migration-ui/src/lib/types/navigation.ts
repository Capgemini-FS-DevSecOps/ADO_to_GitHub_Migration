/** Unified navigation types for feature 008 — Migration UI refactor. */

export type UnifiedTabId = 'dashboard' | 'agent' | 'migrate' | 'discovery' | 'settings';

export interface UnifiedTabDef {
  id: UnifiedTabId;
  label: string;
  href: string;
  icon?: string;
  description: string;
}

export interface DiscoverySubTab {
  id: 'overview' | 'readiness' | 'workflows' | 'validation';
  label: string;
  href: string;
}

export interface MigrateSubTab {
  id: 'migration';
  label: string;
  href: string;
}

export const UNIFIED_TABS: UnifiedTabDef[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    href: '/',
    icon: 'chart',
    description: 'Migration overview and status dashboard',
  },
  {
    id: 'agent',
    label: 'Agent',
    href: '/settings/agent',
    icon: 'bot',
    description: 'Clean, full-page agent chat interface',
  },
  {
    id: 'migrate',
    label: 'Migrate',
    href: '/settings/migrate',
    icon: 'arrow-right-left',
    description: 'On-demand migration and wave management',
  },
  {
    id: 'discovery',
    label: 'Discovery',
    href: '/settings/discovery',
    icon: 'search',
    description: 'Scan ADO organizations, review repositories, pipeline readiness, workflows, and validation',
  },
  {
    id: 'settings',
    label: 'Settings',
    href: '/settings',
    icon: 'settings',
    description: 'Profiles, models, connectivity, and platform configuration',
  },
];

export const DISCOVERY_SUB_TABS: DiscoverySubTab[] = [
  { id: 'overview', label: 'Overview', href: '/settings/discovery' },
  { id: 'readiness', label: 'Pipeline Readiness', href: '/settings/discovery?tab=readiness' },
  { id: 'workflows', label: 'Workflows', href: '/settings/discovery?tab=workflows' },
  { id: 'validation', label: 'Validation', href: '/settings/discovery?tab=validation' },
];

export const MIGRATE_SUB_TABS: MigrateSubTab[] = [
  { id: 'migration', label: 'Migration', href: '/settings/migrate' },
];
