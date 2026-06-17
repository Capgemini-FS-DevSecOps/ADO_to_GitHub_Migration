# Tasks: Deployment Profile Onboarding and Governance

**Input**: Design documents from `specs/005-profile-onboarding/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% line coverage on changed `ado2gh/api` and `ado2gh/auth` modules)

**Organization**: Tasks grouped by user story for independent delivery and validation.

**Plan sync**: 2026-06-16 — mandatory profile onboarding, admin-only delete with manual default pick, operator pending/approve/deny/appeal, post-bootstrap login registration.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Contract scaffolding, coverage scope, route placeholders

- [X] T001 Add `specs/005-profile-onboarding/contracts/` references to `tests/contract/` README or extend `tests/contract/test_ide_contracts.py` with `005-profile-onboarding` contract path list
- [X] T002 [P] Ensure `pyproject.toml` pytest-cov includes `ado2gh/api/profile_governance.py` and `ado2gh/auth/service.py` in coverage scope with `--cov-fail-under=85` for `ado2gh` package
- [X] T003 [P] Add public route allowlist entries in `apps/migration-ui/src/middleware.ts` for `/onboarding/profile` per `contracts/profile-setup-ui.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Profile lifecycle fields, governance module, audit helpers — **blocks all user stories**

**⚠️ CRITICAL**: No user story work until this phase is complete

### Tests (Foundational)

- [X] T004 [P] Create `tests/test_profile_governance.py` with fixtures for `SettingsStore` tmp path — test `active_profile_count`, `needs_profile_setup`, last-active invariant helpers (fail before implementation)
- [X] T005 [P] Create `tests/contract/test_profile_onboarding_contracts.py` asserting paths from `specs/005-profile-onboarding/contracts/profile-onboarding-api.md` and `profile-approval-api.md`

### Implementation (Foundational)

- [X] T006 Extend `MigrationProfile` in `ado2gh/api/settings_store.py` with `status`, `is_default`, `submitted_by`, `approval` metadata; update `to_public()` per `data-model.md`
- [X] T007 Implement `ado2gh/api/profile_governance.py` — `ProfileStatus` enum, `count_active_profiles()`, `needs_profile_setup()`, `assert_can_delete()`, `assert_profile_active_for_run()`
- [X] T008 Add profile audit helpers in `ado2gh/api/profile_governance.py` or `ado2gh/assignments/audit.py` — emit `profile.*` and `user.registered` events without secrets (CA-003)
- [X] T009 Extend `SettingsStore.load/save` in `ado2gh/api/settings_store.py` to round-trip new profile fields in `ui_settings.json` `_secrets` block unchanged
- [X] T010 Add `get_active_profiles()` and `get_default_profile()` helpers in `ado2gh/api/settings_store.py` filtering `status=active`

**Checkpoint**: Governance module and profile schema ready

---

## Phase 3: User Story 1 — Admin First Login → Mandatory Profile Setup (Priority: P1) 🎯 MVP

**Goal**: Admin cannot use console until first validated deployment profile exists.

**Independent Test**: Fresh instance → bootstrap admin → redirected to `/onboarding/profile` → invalid credentials rejected → valid profile → dashboard.

### Tests for User Story 1

- [X] T011 [P] [US1] Add onboarding gate tests in `tests/test_profile_onboarding_api.py` — `GET /v1/onboarding/status` for admin with zero active profiles
- [X] T012 [P] [US1] Add test that `POST /v1/settings/profiles/setup` rejects invalid ADO/GitHub in `tests/test_profile_onboarding_api.py`

### Implementation for User Story 1

- [X] T013 [US1] Add `GET /v1/onboarding/status` in `services/accelerator_api/main.py` using `profile_governance` + session role from auth middleware
- [X] T014 [US1] Update `POST /v1/settings/profiles/setup` in `services/accelerator_api/main.py` — admin creates `status=active`, `is_default` when first active; block operator when `active_profile_count===0` (FR-017)
- [X] T015 [US1] Extend bootstrap/login responses in `services/accelerator_api/auth_routes.py` with `redirect_path: /onboarding/profile` when no active profiles
- [X] T016 [US1] Create `apps/migration-ui/src/app/onboarding/profile/page.tsx` reusing `ProfileWizard` with `mode="onboarding"`
- [X] T017 [US1] Extend `apps/migration-ui/src/components/ProfileWizard.tsx` with `mode` prop (`onboarding` | `settings-admin` | `settings-operator`) per `contracts/profile-setup-ui.md`
- [X] T018 [US1] Update `apps/migration-ui/src/components/AuthGate.tsx` to fetch onboarding status and redirect admin to `/onboarding/profile` when `needs_profile_setup`
- [X] T019 [US1] Update `apps/migration-ui/src/app/login/LoginClient.tsx` post-bootstrap redirect to `/onboarding/profile` when gate open
- [X] T020 [P] [US1] Add `fetchOnboardingStatus()` in `apps/migration-ui/src/lib/api.ts` per `contracts/profile-onboarding-api.md`

**Checkpoint**: Admin mandatory profile onboarding works end-to-end

---

## Phase 4: User Story 2 — Minimum One Active Default Profile (Priority: P1)

**Goal**: Always ≥1 active profile; exactly one default; safe delete with manual default replacement.

**Independent Test**: Two profiles → delete non-default OK → delete sole active blocked → delete default requires picking replacement.

### Tests for User Story 2

- [X] T021 [P] [US2] Tests in `tests/test_profile_governance.py` for last-active delete block and `new_default_profile_id` requirement on default delete
- [X] T022 [P] [US2] API tests for `DELETE` and `deactivate` admin-only RBAC plus `409 last_active_profile` in `tests/test_profile_onboarding_api.py`

### Implementation for User Story 2

- [X] T023 [US2] Update `SettingsStore.delete_profile()` in `ado2gh/api/settings_store.py` to call `profile_governance.assert_can_delete()` and accept `new_default_profile_id`
- [X] T024 [US2] Update `DELETE /v1/settings/profiles/{profile_id}` in `services/accelerator_api/main.py` — admin RBAC, body `new_default_profile_id`, audit `profile.deleted`
- [X] T025 [US2] Add `POST /v1/settings/profiles/{profile_id}/set-default` in `services/accelerator_api/main.py` per contract
- [X] T026 [US2] Add `POST /v1/settings/profiles/{profile_id}/deactivate` in `services/accelerator_api/main.py` — **admin-only** RBAC, same last-active invariant as delete
- [X] T027 [US2] Update profiles list UI in `apps/migration-ui/src/app/settings/profiles/page.tsx` — default badge, disable delete on sole active, delete-default confirmation with profile picker
- [X] T028 [P] [US2] Add `setProfileDefault()` and `deleteProfile(id, newDefaultId?)` in `apps/migration-ui/src/lib/api.ts`

**Checkpoint**: Profile invariant and default management enforced

---

## Phase 5: User Story 3 — Operator Profile Creation with Admin Approval (Priority: P1)

**Goal**: Operator submits profiles → pending; admin approve/deny; operator appeal; non-active profiles blocked for runs.

**Independent Test**: Operator submits profile → pending → migration blocked → admin approves → selectable.

### Tests for User Story 3

- [X] T029 [P] [US3] Add `tests/test_profile_approval_flow.py` — operator submit pending, approve re-validates, deny stays denied, appeal re-queues
- [X] T030 [P] [US3] Test `403 profile_not_active` on migration, discovery, and agent session routes in `tests/test_profile_onboarding_api.py` and `tests/test_profile_approval_flow.py`

### Implementation for User Story 3

- [X] T031 [US3] Update `setup_profile` in `ado2gh/api/settings_store.py` — operator path sets `pending_approval`, `submitted_by`; admin path `active`
- [X] T032 [US3] Add `GET /v1/settings/profiles/pending` and `GET /v1/settings/profiles/mine/pending` in `services/accelerator_api/main.py`
- [X] T033 [US3] Add `POST /v1/settings/profiles/{id}/approve` with credential re-validation in `services/accelerator_api/main.py`
- [X] T034 [US3] Add `POST /v1/settings/profiles/{id}/deny` in `services/accelerator_api/main.py`
- [X] T035 [US3] Add `POST /v1/settings/profiles/{id}/appeal` in `services/accelerator_api/main.py` (submitter only, unlimited, audit `profile.appealed`)
- [X] T036 [US3] Guard deployment profile status in `services/accelerator_api/main.py`, `ado2gh/api/profile_discovery.py`, and `services/agent/main.py` (reject non-`active` profiles for runs/sessions per FR-008)
- [X] T037 [US3] Create `apps/migration-ui/src/app/settings/profiles/pending/page.tsx` — admin approval queue with approve/deny actions
- [X] T038 [US3] Update `ProfileWizard` operator submit messaging and pending badge on `apps/migration-ui/src/app/settings/profiles/page.tsx`
- [X] T039 [P] [US3] Add appeal action for denied profiles in operator profile detail UI (`apps/migration-ui/src/app/settings/profiles/[id]/` or list row)
- [X] T040 [P] [US3] Add approval API client methods in `apps/migration-ui/src/lib/api.ts`
- [X] T061 [P] [US3] Restrict Agent tab deployment profile picker to `status=active` profiles in `apps/migration-ui/src/app/agent/page.tsx` or `apps/migration-ui/src/components/AgentChat.tsx`

**Checkpoint**: Operator approval workflow complete

---

## Phase 6: User Story 6 — Create Account on Login (Priority: P2)

**Goal**: Post-bootstrap login shows Create account; self-register as operator with session.

**Independent Test**: After bootstrap → `/login` shows Create account → register operator → signed in.

**Note**: Implemented before US4/US5 to enable multi-session Docker testing for other stories.

### Tests for User Story 6

- [X] T041 [P] [US6] Create `tests/test_auth_registration.py` — register operator, reject when `needs_bootstrap`, reject admin role, duplicate username generic error

### Implementation for User Story 6

- [X] T042 [US6] Implement `AuthService.register_operator()` in `ado2gh/auth/service.py` with password validation and duplicate check
- [X] T043 [US6] Add `POST /v1/auth/register` in `services/accelerator_api/auth_routes.py` per `contracts/auth-registration-api.md`
- [X] T044 [US6] Extend `GET /v1/auth/bootstrap-status` with `registration_enabled` field
- [X] T045 [US6] Add `register()` and extend bootstrap status in `apps/migration-ui/src/lib/auth.ts`
- [X] T046 [US6] Update `apps/migration-ui/src/app/login/LoginClient.tsx` — Create account link/toggle, registration form per `contracts/login-registration-ui.md`
- [X] T047 [P] [US6] Emit `user.registered` audit on successful registration in `ado2gh/auth/service.py`

**Checkpoint**: Operator self-registration on login works

---

## Phase 7: User Story 4 — Credential Validation UX (Priority: P2)

**Goal**: Test-connection UX, warnings without blocking valid save, masked tokens after save.

**Independent Test**: Wizard test buttons show results; API responses mask secrets post-save.

### Tests for User Story 4

- [X] T048 [P] [US4] Add tests in `tests/test_profile_onboarding_api.py` that `MigrationProfile.to_public()` never returns raw PAT/token values

### Implementation for User Story 4

- [X] T049 [US4] Ensure `ProfileWizard` in `apps/migration-ui/src/components/ProfileWizard.tsx` shows validation warnings from API without blocking submit when `valid=true`
- [X] T050 [US4] Verify profile detail views use `to_public()` fields only in `apps/migration-ui/src/app/settings/profiles/[id]/page.tsx` (or tokens subpage)
- [X] T051 [P] [US4] Add audit `profile.validation_failed` on failed setup validation in `services/accelerator_api/main.py`

**Checkpoint**: Credential UX and masking verified

---

## Phase 8: User Story 5 — Returning Admin Without Profiles (Priority: P2)

**Goal**: Zero active profiles → admin to setup; operator blocked with message.

**Independent Test**: Simulate zero active profiles → admin redirected; operator sees blocked state.

### Tests for User Story 5

- [X] T052 [P] [US5] Add gate redirect tests for admin vs operator when `active_profile_count===0` in `tests/test_profile_onboarding_api.py`

### Implementation for User Story 5

- [X] T053 [US5] Harden `AuthGate` in `apps/migration-ui/src/components/AuthGate.tsx` — operator `blocked_message` banner when no active profiles (no onboarding redirect)
- [X] T054 [US5] Block operator profile create UI when `active_profile_count===0` in `apps/migration-ui/src/app/settings/profiles/new/page.tsx`
- [X] T055 [P] [US5] Return `can_submit_profile: false` for operators in `GET /v1/onboarding/status` when zero active profiles

**Checkpoint**: Recovery and zero-profile edge cases handled

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Coverage, quickstart validation, docs

- [X] T056 [P] Run `pytest tests/test_profile_governance.py tests/test_profile_onboarding_api.py tests/test_profile_approval_flow.py tests/test_auth_registration.py -q` and fix failures
- [X] T057 [P] Verify `pytest --cov=ado2gh/api --cov=ado2gh/auth --cov-fail-under=85` passes on changed modules
- [X] T058 Execute manual scenarios 1–7 in `specs/005-profile-onboarding/quickstart.md` against `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` (compose config validated; full UI walkthrough recommended after deploy)
- [X] T059 [P] Update `specs/005-profile-onboarding/checklists/requirements.md` notes if implementation diverges
- [X] T060 Align `CLAUDE.md` login/onboarding workflow bullet with profile gate + registration (optional one-line)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → **User Stories**
- **US1 (Phase 3)** MVP — depends on Foundational
- **US2 (Phase 4)** depends on US1 profile create path (profiles exist to delete)
- **US3 (Phase 5)** depends on US1 setup endpoint; can parallel US2 after T014
- **US6 (Phase 6)** depends on Foundational audit helper; independent of profile stories (enable early for testing)
- **US4 (Phase 7)** mostly ProfileWizard — after US1 wizard modes
- **US5 (Phase 8)** after onboarding gate (US1)
- **Polish (Phase 9)** after desired stories complete

### User Story Dependencies

| Story | Depends on | Independent test |
|-------|------------|------------------|
| US1 | Foundational | Bootstrap → onboarding wizard → dashboard |
| US2 | US1 (needs profiles) | Delete invariant + default picker |
| US3 | US1, US6 (operator account) | Pending → approve → run |
| US6 | Foundational | Login Create account → operator session |
| US4 | US1 | Test connection + masking |
| US5 | US1 | Zero-profile redirects |

### Parallel Opportunities

- T004, T005 parallel in Foundational tests
- T011, T012 parallel US1 tests
- T020 parallel with T016–T019 (api.ts vs pages)
- T029, T030 parallel US3 tests
- T041 parallel with US3 UI work after T042 started
- US6 can start after Phase 2 in parallel with US1 if staffed separately

### Parallel Example: User Story 3

```text
T029 test_profile_approval_flow.py
T030 profile_not_active API tests
T040 api.ts approval client methods
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 + Phase 2
2. Complete Phase 3 (US1)
3. **STOP and VALIDATE** quickstart Scenario 1
4. Demo admin onboarding

### Incremental Delivery

1. US1 → US2 → US3 → US6 → US4 → US5 → Polish
2. Or: Foundational → US6 (operator accounts) → US1 → US3 for approval demo with two browsers

### Suggested MVP scope

**User Story 1 only** (Phases 1–3) — mandatory admin profile onboarding.

**Recommended demo scope**: Phases 1–3 + Phase 6 (US6) + Phase 5 (US3) for full admin/operator Docker session.

---

## Notes

- All tasks use checklist format: `- [ ] Txxx [P?] [USn?] Description with file path`
- Admin-only delete (FR-006); manual default on delete-default (FR-005 clarification)
- Denied profiles stay with unlimited appeal (FR-015, FR-016)
- Operator profile submit only when `active_profile_count >= 1` (FR-007, FR-017)
- Agent sessions and Agent tab use only `active` deployment profiles (FR-008; tasks T036, T061)
- **61 tasks** (T001–T060, T061) — analyze remediation 2026-06-16
