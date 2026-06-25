import { describe, expect, it } from 'vitest';
import type { PlatformPermissions } from './auth';
import {
  canApproveLiveExecution,
  canManageModels,
  canManageSettings,
  isProfilesReadOnly,
  modelsAccessDeniedMessage,
  modelsPageAllowed,
  operatorSettingsHint,
  SETTINGS_TABS,
  visibleSettingsTabs,
} from './permissions';

const admin: PlatformPermissions = {
  can_coordinate: true,
  can_operate: true,
  can_approve: true,
  can_approve_live_execution: true,
  can_manage_users: true,
  can_manage_settings: true,
  can_manage_models: true,
};

const operator: PlatformPermissions = {
  can_coordinate: false,
  can_operate: true,
  can_approve: false,
  can_approve_live_execution: false,
  can_manage_users: false,
  can_manage_settings: false,
  can_manage_models: false,
};

const approver: PlatformPermissions = {
  can_coordinate: false,
  can_operate: false,
  can_approve: true,
  can_approve_live_execution: true,
  can_manage_users: false,
  can_manage_settings: false,
  can_manage_models: false,
};

describe('platform permission gates (FR-010, SC-002)', () => {
  it('admin can manage settings and models', () => {
    expect(canManageSettings(admin)).toBe(true);
    expect(canManageModels(admin)).toBe(true);
    expect(isProfilesReadOnly(admin)).toBe(false);
  });

  it('operator has read-only profiles and blocked models page', () => {
    expect(isProfilesReadOnly(operator)).toBe(true);
    expect(modelsPageAllowed(operator)).toBe(false);
    expect(canManageSettings(operator)).toBe(false);
  });

  it('approver sees live approvals tab but not models', () => {
    expect(canApproveLiveExecution(approver)).toBe(true);
    expect(modelsPageAllowed(approver)).toBe(false);
    const tabs = visibleSettingsTabs(approver);
    expect(tabs.some((t) => t.href === '/settings/approvals')).toBe(true);
    expect(tabs.some((t) => t.href === '/settings/models')).toBe(false);
  });

  it('operator settings nav hides admin-only tabs', () => {
    const tabs = visibleSettingsTabs(operator);
    const hrefs = tabs.map((t) => t.href);
    expect(hrefs).toContain('/settings/profiles');
    expect(hrefs).not.toContain('/settings/models');
    expect(hrefs).not.toContain('/settings/connectivity');
    expect(hrefs).not.toContain('/settings/cloud-credentials');
    expect(hrefs).not.toContain('/settings/users');
    expect(hrefs).not.toContain('/settings/approvals');
  });

  it('admin settings nav includes models, users, approvals, connectivity', () => {
    const tabs = visibleSettingsTabs(admin);
    const hrefs = tabs.map((t) => t.href);
    expect(hrefs).toContain('/settings/models');
    expect(hrefs).toContain('/settings/connectivity');
    expect(hrefs).toContain('/settings/cloud-credentials');
    expect(hrefs).toContain('/settings/users');
    expect(hrefs).toContain('/settings/approvals');
  });

  it('operator hint explains read-only profiles', () => {
    expect(operatorSettingsHint(operator)).toMatch(/read-only/i);
    expect(operatorSettingsHint(admin)).toBeNull();
  });

  it('models denied message mentions can_manage_models', () => {
    expect(modelsAccessDeniedMessage()).toMatch(/can_manage_models/);
  });

  it('SETTINGS_TABS defines all settings routes', () => {
    expect(SETTINGS_TABS.length).toBeGreaterThanOrEqual(5);
  });
});
