# Feature Specification: Agent PEV Console, Model Onboarding, and Admin/Operator RBAC

**Feature Branch**: `004-agent-pev-rbac`

**Created**: 2026-06-16

**Status**: Draft

**Input**: The agent tab is not implemented with a working Planner–Executor–Validator (PEV) flow and cannot be tested. Admins need to onboard LLM models (e.g., via API keys). Admin and Operator roles must be clearly separated: Admins control Settings (add/remove deployment profiles); Operators execute migrations only with platform approval (admin or approver role). Multiple concurrent sessions must work so different users (admin vs operator) can be signed in at once in Docker deployments.

## Clarifications

### Session 2026-06-16

- Q: Who may approve or deny operator live execution requests on the platform queue? → A: Both platform **admin** and platform **approver** roles (admin configures settings; approver gates live execution without settings access).
- Q: When both platform live approval and assignment phase gate apply, must both pass? → A: **Yes (AND)** — platform admin/approver approval first, then assignment phase gate must pass before live execution proceeds.
- Q: What deployment-profile access should operators have in Settings? → A: **Read-only view** — operators see profiles and details; add/edit/delete disabled with explanation (mutations admin-only).
- Q: What scope does the platform live-approval queue cover? → A: **Unified queue** — operator-initiated live Agent PEV and manual dashboard/pipeline migrate requests share one platform approval queue.
- Q: Which LLM provider types are required for v1 acceptance? → A: **Stub + OpenAI + Anthropic** — stub/offline default when unconfigured; v1 MUST support admin onboarding and agent runtime use of OpenAI-compatible and Anthropic API-key models.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Working Agent PEV Tab (Priority: P1)

An operator or admin opens the Agent tab in the migration console, starts a session, and sees a real planner → executor → validator flow with progress, dry-run by default, and clear outcomes—not a placeholder chat.

**Why this priority**: Without a functional PEV surface, agentic migration cannot be validated or demonstrated; this is the core gap blocking agent testing.

**Independent Test**: Logged-in user opens Agent tab → starts dry-run session → receives plan, execution summary, and validation result wired to the existing agent service and accelerator backend.

**Acceptance Scenarios**:

1. **Given** accelerator and agent services are running, **When** the user starts an agent session from the Agent tab, **Then** the UI shows PEV phases (planning, executing, validating) with status updates—not simulated placeholder text.
2. **Given** a session with `dry_run` default, **When** the executor phase runs, **Then** no live mutations occur without an explicit approval step.
3. **Given** a failed backend connection, **When** the user opens the Agent tab, **Then** actionable remediation guidance is shown (same class of guidance as dashboard API errors).

---

### User Story 2 - Admin Onboards LLM Models (Priority: P1)

A platform admin registers one or more LLM providers/models (e.g., cloud API keys or org-approved endpoints) so agent planning and chat use live reasoning instead of stub-only mode.

**Why this priority**: PEV and agent chat require configurable models; admins must control credentials centrally without committing secrets to the repository.

**Independent Test**: Admin onboards OpenAI-compatible and Anthropic models with API keys → Agent tab model picker lists them → session uses selected provider (or falls back to stub with visible degraded state if key invalid).

**Acceptance Scenarios**:

1. **Given** admin role, **When** admin adds a model provider entry (display name, provider type, secret reference), **Then** the secret is stored securely and never returned in API responses or UI after save.
2. **Given** at least one onboarded model, **When** a user opens the Agent tab, **Then** they can select an approved model for the session.
3. **Given** no models configured, **When** a user starts a session, **Then** stub/offline mode is used with a clear “no live model configured” indicator.
4. **Given** invalid or revoked API credentials, **When** a session requests inference, **Then** the user sees a non-secret error and optional fallback to stub—not a silent failure.
5. **Given** v1 acceptance testing, **When** admin onboards OpenAI-compatible and Anthropic models, **Then** agent sessions can invoke each provider type via API key (stub remains available when none configured).

---

### User Story 3 - Admin vs Operator Role Boundaries (Priority: P1)

Admins manage platform configuration; Operators run migrations and agent sessions but cannot change deployment profiles or onboard models without admin rights. Operator live migrations require approval from a platform **admin** or **approver** (not the operator themselves).

**Why this priority**: User explicitly requires clear separation of duties for enterprise operations and aligns with existing Approver/Coordinator patterns.

**Independent Test**: Two browser sessions (admin + operator) in Docker → admin edits profiles/models; operator blocked from Settings mutations; operator live run blocked until an admin or platform approver approves.

**Acceptance Scenarios**:

1. **Given** operator role, **When** user navigates to Settings → deployment profiles, **Then** profiles and details are visible read-only and add/edit/delete actions are disabled with an explanation.
2. **Given** admin role, **When** user manages deployment profiles, **Then** create, update, and remove profiles succeed and are audited.
3. **Given** operator role, **When** user requests live (non-dry-run) migration or agent execution, **Then** the action pauses for platform approval (admin or approver role) before mutating targets.
4. **Given** admin or platform approver role, **When** that user approves a pending operator live request, **Then** live execution proceeds only if the assignment phase gate also passes (when an assignment is linked); both parties see updated status or a clear gate-failure message.
5. **Given** operator role, **When** user attempts to onboard or remove LLM models, **Then** the request is denied.

---

### User Story 4 - Concurrent Sessions in Docker (Priority: P2)

Multiple users (e.g., admin in one browser, operator in another) maintain independent authenticated sessions against the same Docker Compose stack without session bleed or forced logout.

**Why this priority**: Role-boundary testing and realistic ops workflows require parallel logins on a shared local/prod-like instance.

**Independent Test**: Two browsers (or profiles) log in as different users on `docker compose up` → each sees correct role UI → actions are attributed to the correct user in audit records.

**Acceptance Scenarios**:

1. **Given** two valid user accounts, **When** both log in concurrently from separate browsers, **Then** each retains an independent session until expiry or logout.
2. **Given** concurrent sessions, **When** an admin or platform approver approves operator’s live request, **Then** only the operator’s pending action is affected; the approver’s session remains active.
3. **Given** container restart with persistent user store, **When** users exist, **Then** bootstrap is not re-enabled and existing sessions are invalidated gracefully (re-login required).

---

### User Story 5 - Operator Agent Workflow with Approval (Priority: P2)

An operator uses the Agent tab to plan and dry-run a migration assignment—or initiates live migrate from the dashboard/pipeline—and requesting live execution creates an entry on the unified platform approval queue visible to platform admins and approvers.

**Why this priority**: Connects PEV UI to governance already required for manual migration paths.

**Independent Test**: Operator dry-run session succeeds → request live → admin or approver sees approval queue → approve → executor runs live tools → validator reports outcome.

**Acceptance Scenarios**:

1. **Given** operator session, **When** operator completes dry-run PEV, **Then** “Request live execution” is available and records intent without immediate mutation.
2. **Given** pending approval, **When** an admin or platform approver approves with reason, **Then** operator session resumes executor with live flag and audit captures approver identity.
3. **Given** pending approval, **When** an admin or platform approver denies, **Then** operator sees denial reason and session remains in safe (dry-run or completed) state.

---

### Edge Cases

- Admin deletes a deployment profile still referenced by an active assignment: blocked or requires explicit reassignment with clear error.
- Operator session open while admin revokes model credentials: next inference degrades with user-visible message; no secret echoed.
- API key entry mistakes (leading/trailing spaces): normalized or rejected with validation message before save.
- Session expiry during long PEV run: user prompted to re-authenticate; in-progress dry-run state recoverable or clearly abandoned.
- Only stub model available: PEV still completes for testing planner/validator paths without cloud spend.
- Platform approval recorded but assignment phase gate fails: live execution blocked with clear error; audit records both platform approval and gate outcome.
- Concurrent bootstrap or duplicate admin approval clicks: single winner; no double live execution.

## Requirements *(mandatory)*

### Constitution Alignment

This feature touches agent execution, migration approval, and settings management:

- **CA-001**: Agent and migration mutations default to dry-run/preview; live execution requires explicit approval.
- **CA-002**: Live migrate, rollback, profile deletion, and model credential changes require platform **admin** or **approver** approval (with documented reason where applicable).
- **CA-003**: API keys and tokens MUST NOT appear in UI transcripts, logs, audit payloads, or API responses after initial save.
- **CA-004**: Profile changes, model onboarding, session starts, approvals, and live executions MUST produce auditable records with actor, role, and outcome.

### Functional Requirements

- **FR-001**: The Agent tab MUST implement a real PEV user experience connected to the agent service (not placeholder-only chat).
- **FR-002**: PEV sessions MUST default to dry-run for migration mutation tools consistent with hosted agent guardrails.
- **FR-003**: Admins MUST be able to register, update, and remove LLM model configurations including secure storage of provider credentials. v1 MUST support **OpenAI-compatible** and **Anthropic** provider types (plus stub/offline) for acceptance.
- **FR-004**: Model credentials MUST be referenced by the agent runtime without exposing secret values to non-admin users or client-side storage beyond session cookies.
- **FR-005**: The Agent tab MUST allow selection of an onboarded model when at least one is available; stub mode MUST remain available when none are configured.
- **FR-006**: Platform role **admin** MUST be defined as: full settings control (deployment profiles add/remove), model onboarding, user management (per login-bootstrap), and ability to approve operator live requests. Platform role **approver** MUST be defined as: approve/deny operator live execution requests and view the approval queue—without settings, model, or user management rights.
- **FR-007**: Platform role **operator** MUST be defined as: execute dry-run migrations and agent PEV sessions, view operational dashboards, and request live execution—but NOT create/delete deployment profiles or manage LLM credentials.
- **FR-008**: Operator-initiated live migration (dashboard, pipeline, or Agent tab) or agent execution MUST require approval from a platform **admin** or **approver** (not self-approval) on the **unified platform live queue** before irreversible steps run.
- **FR-009**: Platform **admin** and **approver** roles MUST have a visible **unified** approval queue for all pending operator live requests (Agent PEV, manual migrate, and pipeline live runs) with approve/deny and reason.
- **FR-010**: Settings UI for deployment profiles MUST allow operators **read-only** access (view profiles and details); create, update, and delete MUST be restricted to admin role with disabled controls and explanation for operators.
- **FR-011**: The system MUST support multiple concurrent authenticated sessions (distinct users) on a single Docker Compose deployment without shared session state.
- **FR-012**: Audit records MUST attribute actions to the authenticated username and role for agent sessions, approvals, profile changes, and model onboarding.
- **FR-013**: When agent or accelerator backend is unreachable, the Agent tab MUST surface remediation steps consistent with other console surfaces.
- **FR-014**: Existing Coordinator and Approver assignment RBAC MUST remain compatible. When an operator live request is assignment-linked, **both** gates MUST pass in order: (1) platform admin or approver approval on the live queue, then (2) assignment phase gate check—live execution proceeds only if both succeed.

### Key Entities

- **LLMModelConfig**: Admin-managed model entry (display name, provider type [`stub`, `openai`, `anthropic`, `offline`], secret reference, enabled flag, default-for-agent flag).
- **AgentPEVSession**: UI-bound session mirroring agent service state (phases, dry_run, selected model, assignment context).
- **LiveExecutionApproval**: Pending operator request for live migrate/agent run or pipeline live run (requester, target scope, status, approver, reason, timestamps); all operator live paths enqueue here.
- **PlatformRoleCapability**: Mapping of admin, approver, and operator to settings, models, migrations, and live-execution approvals (admin: settings + approve; approver: approve only; operator: execute + request live).
- **ConcurrentAuthSession**: Per-browser session bound to one user; isolated from other sessions on same instance.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tester completes an end-to-end dry-run PEV session from the Agent tab in under 5 minutes with accelerator and agent containers running.
- **SC-002**: 100% of operator attempts to add/remove deployment profiles are blocked without admin role.
- **SC-003**: 100% of operator live execution requests remain blocked until a platform admin or approver records approval (no silent live mutations).
- **SC-004**: Admin can onboard OpenAI-compatible and Anthropic models and an operator can select either in the Agent tab within the same Docker stack without restarting containers.
- **SC-005**: Two concurrent browser sessions (admin + operator) remain authenticated for at least 30 minutes of parallel use without cross-user state leakage.
- **SC-006**: Zero occurrences of API key or token material in UI chat, browser network responses (after save), or audit event payloads in the documented test checklist.
- **SC-007**: 90% of pilot users correctly identify which actions are admin-only vs operator-allowed using in-product role hints or settings labels.

## Assumptions

- Platform login and bootstrap from `002-login-bootstrap` (or equivalent) are available; this feature extends RBAC rather than replacing authentication.
- Agent HTTP service and accelerator from `001-agentic-migration-platform` / `003-local-agent-ide` remain the integration backbone; this feature wires the **web Agent tab** and **admin model registry**.
- “Onboard models through API keys” means admin enters credentials via a secured settings UI stored server-side; OAuth for model vendors is out of scope for v1. **OpenAI-compatible** and **Anthropic** adapters are in scope and required for v1 acceptance; stub/offline remains the default when no models are configured.
- Platform **approver** role (login-bootstrap) may approve operator live requests without settings access; assignment-scoped Approver RBAC remains separate. When assignment-linked, **both** platform approval and assignment phase gate are required (AND), in that order.
- Docker Compose with Postgres prod overlay is the reference multi-user test environment; Kubernetes parity is desirable but not mandatory for v1 acceptance.

## Dependencies

- `002-login-bootstrap`: platform users, sessions, admin bootstrap, role field on users.
- `001-agentic-migration-platform`: PEV agent service, assignments, approval concepts, audit store.
- `003-local-agent-ide`: local agent profiles, tool catalog, stub LLM (extended by live model registry).
