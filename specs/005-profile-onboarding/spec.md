# Feature Specification: Deployment Profile Onboarding and Governance

**Feature Branch**: `005-profile-onboarding`

**Created**: 2026-06-16

**Status**: Clarified (implemented)

**Input**: Once an admin is created they should be taken to a login flow where they must create a profile (ADO credentials) which are validated and GitHub PAT which must also be valid. There should always be at least 1 active default profile. Users can delete any but 1. Operators can create a profile but it must be approved by admin.

**Plan addendum (2026-06-16)**: After initial boot (bootstrap complete), the login screen MUST offer an option to create a platform account (self-registration).

## Clarifications

### Session 2026-06-16

- Q: When an admin denies an operator-submitted profile, what should happen to that profile record? → A: Keep profile with `denied` status; submitter sees denial reason; operator may **appeal**, returning the profile to `pending_approval` in the admin queue.
- Q: Who is allowed to delete deployment profiles (subject to the last-active rule)? → A: **Admin only** — operators cannot delete profiles.
- Q: When admin deletes the current default profile, how is the new default chosen? → A: Admin **must select** replacement default in a confirmation step before delete proceeds.
- Q: When tenant has zero active profiles, can operator submit a profile for approval? → A: **No** on fresh/zero-active tenant — **admin must create the first active profile** via mandatory onboarding after login; operators may submit pending profiles only after at least one active profile exists.
- Q: How many times can an operator appeal a denied profile? → A: **Unlimited** appeals; each appeal re-queues to `pending_approval` and is audited.
- Q: (Plan) After initial boot, should login offer account creation? → A: **Yes** — post-bootstrap login shows **Create account** for self-registration as **operator** (not admin).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Admin First Login → Mandatory Profile Setup (Priority: P1)

A newly bootstrapped admin signs in for the first time and is guided through creating the first deployment profile before accessing the main console. ADO org credentials and GitHub personal access token are validated live; the profile cannot be saved until both pass.

**Why this priority**: Migrations cannot run without validated source and target credentials; blocking the console until the first profile exists prevents empty-state failures.

**Independent Test**: Fresh instance → bootstrap admin → sign in → redirected to profile setup wizard → invalid ADO/PAT rejected → valid credentials saved → admin reaches dashboard with one active default profile.

**Acceptance Scenarios**:

1. **Given** a fresh instance with only the bootstrap admin account, **When** admin signs in, **Then** they are directed to profile setup and cannot reach operational pages (dashboard, migrations, agent) until a profile is successfully created.
2. **Given** profile setup in progress, **When** admin submits invalid ADO credentials, **Then** setup fails with a clear validation message and no profile is persisted.
3. **Given** profile setup in progress, **When** admin submits valid ADO but invalid GitHub PAT, **Then** setup fails with a clear validation message and no profile is persisted.
4. **Given** valid ADO and GitHub credentials, **When** admin completes setup, **Then** a deployment profile is created, marked active, and designated as the default profile.
5. **Given** admin completed first profile setup, **When** they navigate the console on subsequent logins, **Then** they are not forced through setup again unless no active profiles remain.

---

### User Story 2 - Minimum One Active Default Profile (Priority: P1)

The platform always maintains at least one active deployment profile designated as default. Admins may remove or deactivate profiles only when another active profile remains.

**Why this priority**: User explicitly requires a always-on default profile and delete-any-but-one rule for operational safety.

**Independent Test**: Two profiles exist → attempt delete/deactivate both → second action blocked → default badge moves predictably when default is removed.

**Acceptance Scenarios**:

1. **Given** exactly one active profile, **When** an **admin** attempts to delete or deactivate it, **Then** the action is blocked with explanation that at least one active profile is required.
2. **Given** multiple active profiles, **When** admin deletes a non-default profile, **Then** deletion succeeds and remaining profiles stay active.
3. **Given** multiple active profiles, **When** admin deletes the current default profile, **Then** admin MUST choose which remaining active profile becomes default in a confirmation step before deletion completes.
4. **Given** zero profiles (edge recovery), **When** any authenticated admin signs in, **Then** they are redirected to mandatory profile setup until one active default exists.

---

### User Story 3 - Operator Profile Creation with Admin Approval (Priority: P1)

An operator may initiate creation of a new deployment profile (ADO + GitHub credentials) but the profile is not active for migrations until an admin reviews and approves it.

**Why this priority**: Separates credential onboarding from privileged activation while allowing operators to prepare profiles for review.

**Independent Test**: Operator submits profile wizard → profile appears as pending → operator cannot run migrations against it → admin approves → profile becomes active and selectable.

**Acceptance Scenarios**:

1. **Given** operator role, **When** operator completes profile setup with valid ADO and GitHub credentials, **Then** a profile is stored in pending-approval state and is not the default nor usable for migration runs.
2. **Given** pending operator profile, **When** operator attempts to start a migration or agent session against it, **Then** the action is blocked with “awaiting admin approval.”
3. **Given** pending operator profile, **When** admin approves with optional note, **Then** profile becomes active, appears in profile picker, and audit records approver and timestamp.
4. **Given** pending operator profile, **When** admin denies, **Then** profile moves to `denied` status (not deleted), operator sees denial reason, and profile is not usable for migrations.
5. **Given** denied operator profile, **When** operator submits an appeal, **Then** profile returns to `pending_approval` in the admin queue for re-review.
6. **Given** admin role, **When** admin creates a profile, **Then** it is active immediately after successful credential validation (no approval queue).

---

### User Story 4 - Credential Validation UX (Priority: P2)

During profile setup, users test ADO and GitHub connectivity before final save, with actionable feedback (org access, scope warnings) without exposing secrets after save.

**Why this priority**: Validation is required by the user; clear UX reduces support burden and failed migrations.

**Independent Test**: Use test-connection actions in wizard → see success/failure messages → saved profile never echoes raw tokens in UI or audit.

**Acceptance Scenarios**:

1. **Given** profile setup, **When** user runs “test ADO connection,” **Then** result shows success with project/repo counts or failure with non-secret reason.
2. **Given** profile setup, **When** user runs “test GitHub connection,” **Then** result shows success with user/org visibility or failure with non-secret reason.
3. **Given** saved profile, **When** user views profile details, **Then** tokens are masked and not retrievable in full from the UI.

---

### User Story 5 - Returning Admin Without Profiles (Priority: P2)

If all profiles were removed in error (admin-only recovery path) or data was restored without profiles, admins are guided back through setup; operators see a read-only message to contact an admin.

**Why this priority**: Covers edge cases while enforcing minimum-profile invariant.

**Independent Test**: Simulate zero profiles → admin redirected to setup; operator sees blocked state.

**Acceptance Scenarios**:

1. **Given** no active profiles and admin session, **When** admin loads any protected route, **Then** redirect to profile setup.
2. **Given** no active profiles and operator session, **When** operator loads console, **Then** they see that no profile is available, cannot submit new profiles until an admin has created at least one active profile, and cannot run migration actions.

---

### Edge Cases

- ADO valid but GitHub org name mismatch: validation warns; save allowed only if GitHub token validation passes for declared target org.
- Operator pending profile with credentials that expire before approval: admin approval re-validates or warns before activation.
- Denied profile appeal: operator may appeal unlimited times; each appeal re-queues and emits audit event; re-approval still re-validates credentials.
- Concurrent delete of two profiles when only two exist: only one delete succeeds; second blocked.
- Default profile switch during active migration: in-flight runs keep original profile context; new runs use updated default.
- Bootstrap admin creates profile then creates operator: operator never bypasses first-login profile gate if no profiles exist (only admin can establish first profile on fresh instance).

---

### User Story 6 - Create Account on Login (Post-Bootstrap) (Priority: P2)

After the first admin account exists (bootstrap complete), visitors on the login page can create their own platform account without contacting an admin first. New self-registered users receive the **operator** role by default and can sign in immediately.

**Why this priority**: Enables multi-user Docker testing (admin + operator in separate browsers) and operator onboarding without admin-only user management UI as a prerequisite.

**Independent Test**: Bootstrap admin exists → open `/login` → **Create account** → register operator → sign in → operator session active (subject to profile onboarding rules).

**Acceptance Scenarios**:

1. **Given** bootstrap is complete (`needs_bootstrap === false`), **When** user opens `/login`, **Then** a **Create account** option is visible alongside sign-in.
2. **Given** create-account flow, **When** user submits valid username and password, **Then** account is created with **operator** role and user is signed in (session cookie set).
3. **Given** create-account flow, **When** user attempts to self-register as admin or select admin role, **Then** registration is rejected.
4. **Given** fresh instance (`needs_bootstrap === true`), **When** user opens `/login`, **Then** only bootstrap admin creation is shown (no separate create-account for additional users until bootstrap completes).
5. **Given** duplicate username, **When** user registers, **Then** generic validation error without revealing whether username exists (same class as login failures).

## Requirements *(mandatory)*

### Constitution Alignment

- **CA-001**: Profile setup is not a migration mutation; no dry-run path required for validation-only steps.
- **CA-002**: Deleting the last active profile and approving operator profiles are privileged actions requiring admin role; denials and deletions SHOULD record reason where applicable.
- **CA-003**: ADO PAT and GitHub tokens MUST NOT appear in logs, audit events, UI transcripts, or API responses after initial submission.
- **CA-004**: Profile create, approve, deny, delete, default change, and validation attempts MUST be auditable with actor, role, profile id, and outcome.

### Functional Requirements

- **FR-001**: After bootstrap admin account creation, the sign-in flow MUST require successful creation of at least one validated deployment profile before granting access to main console features.
- **FR-002**: Profile creation MUST validate ADO organization URL and PAT against live Azure DevOps before persisting.
- **FR-003**: Profile creation MUST validate GitHub PAT against live GitHub before persisting; invalid tokens MUST block save.
- **FR-004**: The system MUST always retain at least one active deployment profile; delete and deactivate operations MUST be rejected when they would leave zero active profiles.
- **FR-005**: Exactly one active profile MUST be designated as default at any time when profiles exist. When deleting the current default, admin MUST select the replacement default in a confirmation step before deletion completes.
- **FR-006**: Only **admin** role MAY delete deployment profiles; operators MUST NOT delete profiles. Deletion MUST be rejected when it would violate the minimum-one-active rule.
- **FR-007**: Operators MUST be able to submit new deployment profiles through the same credential validation wizard as admins **only when at least one active profile already exists**.
- **FR-017**: On a fresh instance or when `active_profile_count === 0`, only **admin** MAY create profiles; operator profile submission MUST be blocked until an admin establishes the first active profile.
- **FR-008**: Operator-submitted profiles MUST enter pending-approval state and MUST NOT be used for migrations, discovery runs, or agent sessions until admin approval.
- **FR-009**: Admins MUST have a queue or list of pending operator profiles with approve and deny actions.
- **FR-015**: Denied operator profiles MUST remain stored with `denied` status and visible denial reason to the submitting operator; records MUST NOT be deleted on deny.
- **FR-016**: Submitting operators MUST be able to appeal a denied profile, returning it to `pending_approval` and re-appearing in the admin approval queue; appeals are **unlimited** and each appeal MUST be audited.
- **FR-010**: Admin-created profiles MUST become active immediately upon successful validation (no approval step).
- **FR-011**: Authenticated users with zero active profiles MUST be handled per role: admins directed to setup; operators informed and blocked from migration actions.
- **FR-012**: Profile setup wizard MUST support test-connection for ADO and GitHub independently before final submit.
- **FR-013**: Credential validation outcomes MUST surface warnings (e.g., missing scopes, org not visible) without failing save when validation is otherwise valid.
- **FR-014**: This feature extends platform login (`002-login-bootstrap`) and complements profile RBAC in `004-agent-pev-rbac` without removing assignment-scoped profile roles where they already apply.
- **FR-018**: When bootstrap is complete, the login page MUST expose a **Create account** path for self-registration.
- **FR-019**: Self-registered accounts MUST default to **operator** role; **admin** role MUST NOT be available via self-registration.
- **FR-020**: Successful self-registration MUST establish an authenticated session (same as login) and emit an auditable `user.registered` event.

### Key Entities

- **DeploymentProfile**: Named migration context (ADO org, stored ADO credential, GitHub org, stored GitHub credential, active flag, default flag, approval status).
- **ProfileApprovalRequest**: Operator-initiated profile pending admin decision (submitter, timestamps, status, approver, reason); may cycle through `pending_approval` → `denied` → appeal → `pending_approval`.
- **CredentialValidationResult**: Outcome of live ADO/GitHub tests (valid flag, user-facing message, non-secret metadata such as project counts).
- **ProfileOnboardingGate**: Derived state blocking console until minimum profile invariant satisfied for the tenant.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: New admin completes first validated profile setup and reaches dashboard in under 10 minutes on a reference Docker deployment.
- **SC-002**: 100% of save attempts with invalid ADO or GitHub credentials are rejected before persistence.
- **SC-003**: 100% of attempts to delete or deactivate the sole active profile are blocked.
- **SC-004**: Operator-created profiles remain unusable for migration until admin approval in 100% of tested flows.
- **SC-005**: Zero raw ADO or GitHub tokens in audit payloads, browser network responses (post-save), or application logs in the documented test checklist.
- **SC-006**: After admin approval, operator-submitted profile is selectable for migrations within one user session without container restart.
- **SC-007**: 90% of pilot users correctly understand why they are blocked at profile setup versus main console (in-product copy or wizard labels).
- **SC-008**: Every profile appeal emits an auditable event with actor, profile id, and timestamp (100% in automated appeal-flow tests).
- **SC-009**: Post-bootstrap login page presents Create account; operator self-registration completes and yields active session in under 2 minutes in reference Docker deployment.

## Assumptions

- “Profile” means deployment/migration profile (ADO source + GitHub target credentials), not a user profile or LLM model profile.
- First profile on a fresh instance MUST be created by admin via mandatory onboarding after login; operators cannot submit profiles until at least one active profile exists.
- Validation uses live connectivity checks to Azure DevOps and GitHub (same behavior class as existing test-connection flows).
- Pending operator profiles may be visible to the submitting operator but not to other operators unless sharing is defined in plan phase (default: submitter + admins see pending items).
- Profile deletion is soft or hard per existing settings store; invariant is “at least one active,” not “at least one row in database.”
- Re-validation on admin approval is recommended but failure blocks activation rather than silently approving stale credentials.

## Dependencies

- `002-login-bootstrap`: platform users, admin bootstrap, sign-in flow, session roles.
- `004-agent-pev-rbac`: admin vs operator settings boundaries and approval patterns (aligned, not duplicated).
- Existing deployment profile storage and credential validation behavior in the accelerator settings surface.
