import type { PlatformPermissions } from './auth';

export function canManageSettings(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_manage_settings === true;
}

export function canManageModels(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_manage_models === true;
}

export function canApproveLiveExecution(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_approve_live_execution === true;
}

export function canOperate(permissions?: PlatformPermissions | null): boolean {
  return permissions?.can_operate === true;
}

/** Operator (or approver) read-only profile access — view allowed, mutations blocked. */
export function isProfilesReadOnly(permissions?: PlatformPermissions | null): boolean {
  return canOperate(permissions) && !canManageSettings(permissions);
}

export function modelsPageAllowed(permissions?: PlatformPermissions | null): boolean {
  return canManageModels(permissions);
}

export type SettingsTabDef = {
  href: string;
  label: string;
  hint: string;
  cap?: keyof PlatformPermissions;
};

export const SETTINGS_TABS: SettingsTabDef[] = [
  { href: '/settings/profiles', label: 'Profiles', hint: 'View & manage deployment profiles' },
  { href: '/settings/advanced', label: 'Advanced', hint: 'Global migration settings' },
  { href: '/settings/models', label: 'LLM models', cap: 'can_manage_models', hint: 'Admin only' },
  {
    href: '/settings/connectivity',
    label: 'Connectivity',
    cap: 'can_manage_models',
    hint: 'Proxy, CA, model override toggle',
  },
  {
    href: '/settings/approvals',
    label: 'Live approvals',
    cap: 'can_approve_live_execution',
    hint: 'Admin or approver',
  },
  { href: '/settings/users', label: 'Users', cap: 'can_manage_users', hint: 'Admin only' },
];

export function visibleSettingsTabs(permissions?: PlatformPermissions | null): SettingsTabDef[] {
  return SETTINGS_TABS.filter((tab) => {
    if (!tab.cap) return true;
    return permissions?.[tab.cap] === true;
  });
}

export function operatorSettingsHint(permissions?: PlatformPermissions | null): string | null {
  if (!isProfilesReadOnly(permissions)) return null;
  return 'Operator view — deployment profiles are read-only; live execution requires admin or approver approval.';
}

export function modelsAccessDeniedMessage(): string {
  return 'LLM model onboarding is restricted to platform administrators (`can_manage_models`).';
}

export function profilesReadOnlyHint(): string {
  return 'Operators have read-only access; profile changes require an admin.';
}
