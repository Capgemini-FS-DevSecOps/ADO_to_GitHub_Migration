import type { PlatformPermissions } from './auth';

/** Whether the session may change platform settings. */
export function canManageSettings(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_manage_settings === true;
}

/** Whether the session may onboard and edit LLM models. */
export function canManageModels(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_manage_models === true;
}

/** Whether the session may approve a live (non dry-run) execution. */
export function canApproveLiveExecution(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_approve_live_execution === true;
}

/** Whether the session may run migrations as an operator. */
export function canOperate(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_operate === true;
}

/** Operator (or approver) read-only profile access — view allowed, mutations blocked. */
export function isProfilesReadOnly(permissions?: PlatformPermissions | null): boolean {
  return canOperate(permissions) && !canManageSettings(permissions);
}

/** Whether the LLM models page should be reachable for this session. */
export function modelsPageAllowed(permissions?: PlatformPermissions | null): boolean {
  return canManageModels(permissions);
}

/** One settings tab: its route, label, hint text, and the capability it requires, if any. */
export type SettingsTabDef = {
  href: string;
  label: string;
  hint: string;
  cap?: keyof PlatformPermissions;
};

/** Every settings tab in display order; a tab with a `cap` is hidden without that permission. */
export const SETTINGS_TABS: SettingsTabDef[] = [
  { href: '/settings/profiles', label: 'Profiles', hint: 'View & manage deployment profiles' },
  { href: '/settings/history', label: 'History', hint: 'Past migration runs and audit history' },
  { href: '/settings/advanced', label: 'Advanced', hint: 'Global migration settings' },
  { href: '/settings/models', label: 'LLM models', cap: 'can_manage_models', hint: 'Admin only' },
  {
    href: '/settings/connectivity',
    label: 'Connectivity',
    cap: 'can_manage_models',
    hint: 'Proxy, CA, model override toggle',
  },
  {
    href: '/settings/cloud-credentials',
    label: 'Cloud credentials',
    cap: 'can_manage_models',
    hint: 'Approve ambient AWS, Foundry, or GCP LLM credentials',
  },
  {
    href: '/settings/approvals',
    label: 'Live approvals',
    cap: 'can_approve_live_execution',
    hint: 'Admin or approver',
  },
  { href: '/settings/users', label: 'Users', cap: 'can_manage_users', hint: 'Admin only' },
];

/** The settings tabs this session may see, dropping any whose required capability is missing. */
export function visibleSettingsTabs(permissions?: PlatformPermissions | null): SettingsTabDef[] {
  return SETTINGS_TABS.filter((tab) => {
    if (!tab.cap) return true;
    return permissions?.[tab.cap] === true;
  });
}

/** Banner text explaining read-only settings to operators, or null when there is nothing to say. */
export function operatorSettingsHint(permissions?: PlatformPermissions | null): string | null {
  if (!isProfilesReadOnly(permissions)) return null;
  return 'Operator view — deployment profiles are read-only; live execution requires admin or approver approval.';
}

/** Whether the session may read audit history for every user rather than only its own. */
export function canViewAllAuditHistory(permissions?: PlatformPermissions | null, role?: string | null): boolean {
  return role === 'admin' || permissions?.can_manage_settings === true;
}

/** Message shown when someone without model permissions opens the LLM models page. */
export function modelsAccessDeniedMessage(): string {
  return 'LLM model onboarding is restricted to platform administrators (`can_manage_models`).';
}

/** Message shown to operators beside profile controls they are not allowed to use. */
export function profilesReadOnlyHint(): string {
  return 'Operators have read-only access; profile changes require an admin.';
}
