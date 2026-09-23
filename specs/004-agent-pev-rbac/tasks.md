# Tasks: Agent PEV Console, Model Onboarding, and Admin/Operator RBAC

**Input**: Design documents from `specs/004-agent-pev-rbac/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% line coverage on changed `ado2gh/auth`, `ado2gh/api`, `ado2gh/agents`, `services/agent`, `services/accelerator_api`)

**Organization**: Tasks grouped by user story for independent delivery and validation.

**Plan sync (2026-06-16)**: Admin **or** approver live queue; **unified queue** (Agent + dashboard migrate + pipeline); **dual AND gates**; operator **read-only** profiles; **OpenAI + Anthropic** v1 acceptance. Regenerated after `/speckit-analyze` remediation.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Contract scaffolding, coverage scope, feature context

- [x] T001 Confirm `.specify/feature.json` points to `specs/004-agent-pev-rbac`
- [x] T002 [P] Create `tests/contract/test_004_agent_pev_contracts.py` listing paths from `specs/004-agent-pev-rbac/contracts/`
- [x] T003 [P] Verify `pyproject.toml` / CI runs pytest with `--cov=ado2gh --cov-fail-under=85` for modules touched by this feature

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Platform capabilities, StateDB approval table, shared RBAC helpers — **blocks all user stories**

**⚠️ CRITICAL**: No user story work until this phase is complete

### Tests (Foundational)

- [x] T004 [P] Create `tests/test_platform_rbac.py` — assert `permissions_for()` matrix incl. approver `can_approve_live_execution` and no `can_operate` per `contracts/platform-rbac.md`
- [x] T005 [P] Create `tests/test_live_approval_queue.py` — CRUD, idempotent pending, `scope_type` variants per `data-model.md` (fail before implementation)

### Implementation (Foundational)

- [x] T006 Extend `permissions_for()` in `ado2gh/auth/service.py` with `can_manage_settings`, `can_manage_models`, `can_approve_live_execution`
- [x] T007 Create `ado2gh/api/platform_rbac.py` — `require_capability()`, `get_platform_user()` with module + function docstrings
- [x] T008 Add `live_execution_approvals` table and CRUD methods in `ado2gh/state/db.py` per `data-model.md`
- [x] T009 [P] Add Postgres parity for `live_execution_approvals` in `ado2gh/state/postgres_db.py`
- [x] T010 Create `ado2gh/api/live_approval_store.py` — create/list/approve/deny/resume-by-scope with audit emission (no secrets in payload)
- [x] T011 Extend `GET /v1/auth/session` permissions payload in `services/accelerator_api/auth_routes.py` per `contracts/platform-rbac.md`
- [x] T012 Add agent auth middleware in `services/agent/main.py` — validate `ado2gh_session` via `AuthService` when `ADO2GH_AUTH_ENABLED=true`

**Checkpoint**: RBAC capabilities and unified approval store ready

---

## Phase 3: User Story 1 — Working Agent PEV Tab (Priority: P1) 🎯 MVP

**Goal**: Real planner → executor → validator flow in Agent tab; dry-run default; remediation on backend failure.

**Independent Test**: Logged-in user opens `/agent` → starts dry-run session → sees PEV phases complete without placeholder text (SC-001).

### Tests for User Story 1

- [x] T013 [P] [US1] Create `tests/test_agent_pev_live_gate.py` — dry-run completes without live migrate; live blocked until platform approval recorded (fail first)
- [x] T014 [P] [US1] Add agent health/remediation contract tests in `tests/contract/test_004_agent_pev_contracts.py` per `contracts/agent-pev-ui-api.md`

### Implementation for User Story 1

- [x] T015 [US1] Fix PEV live execution order in `services/agent/main.py` — dry-run completes → `request-live` → approval → then `pev_loop(dry_run=False)` (CA-001)
- [x] T016 [P] [US1] Pass `model_id` on session create in `apps/migration-ui/src/lib/agent.ts` with `credentials: 'include'`
- [x] T017 [US1] Wire model picker, PEV phase labels, dry-run badge, and poll loop in `apps/migration-ui/src/components/AgentChat.tsx`
- [x] T018 [US1] Remove placeholder copy and add operational guidance in `apps/migration-ui/src/app/agent/page.tsx`
- [x] T019 [US1] Extend session request/response with `selected_model_id`, `llm_degraded`, PEV status fields in `services/agent/main.py`
- [x] T020 [US1] Surface `fetchAgentHealth()` remediation steps in `apps/migration-ui/src/components/AgentChat.tsx` when agent/accelerator unreachable (FR-013)

**Checkpoint**: End-to-end dry-run PEV from Agent tab in under 5 minutes (SC-001)

---

## Phase 4: User Story 2 — Admin Onboards LLM Models (Priority: P1)

**Goal**: Admin registers OpenAI-compatible and Anthropic models; secrets masked; Agent tab selects onboarded model or shows stub indicator.

**Independent Test**: Admin onboards **both** provider types → operator selects each in Agent picker (SC-004, SC-006).

### Tests for User Story 2

- [x] T021 [P] [US2] Create `tests/test_llm_model_runtime.py` — OpenAI + Anthropic resolution, secrets never in API response, stub fallback on invalid key
- [x] T022 [P] [US2] Add contract tests for `GET/POST/PUT/DELETE /v1/settings/llm-models` in `tests/contract/test_004_agent_pev_contracts.py`

### Implementation for User Story 2

- [x] T023 [P] [US2] Add `default_for_agent` field, api_key trim validation, single-default rule in `ado2gh/api/llm_model_store.py`
- [x] T024 [US2] Wire `get_llm_provider(model_id)` to load secrets from `LLMModelStore` in `ado2gh/agents/llm_provider.py`
- [x] T025 [P] [US2] Add OpenAI-compatible and Anthropic HTTP adapters with degraded stub fallback in `ado2gh/agents/llm_provider.py`
- [x] T026 [US2] Extend LLM CRUD routes with audit events in `services/accelerator_api/main.py` per `contracts/llm-model-api.md`
- [x] T027 [US2] Complete admin onboarding UI (add/edit/delete, masked key) in `apps/migration-ui/src/app/settings/models/page.tsx`
- [x] T028 [US2] Show “no live model configured” / degraded LLM indicator in `apps/migration-ui/src/components/AgentChat.tsx`
- [x] T029 [P] [US2] Extend `GET /v1/llm/status` in `services/agent/main.py` with `models_configured` and `default_model_id`

**Checkpoint**: Both OpenAI and Anthropic models onboarded and selectable without container restart (SC-004)

---

## Phase 5: User Story 3 — Admin vs Operator Role Boundaries (Priority: P1)

**Goal**: Operators read-only on profiles; cannot mutate profiles or models; admins full control; role hints visible.

**Independent Test**: Operator **views** profiles read-only; mutations blocked; admin succeeds (SC-002, FR-010).

### Tests for User Story 3

- [x] T030 [P] [US3] Extend `tests/test_platform_rbac.py` — operator `403` on profile/LLM mutations; operator `200` on `GET /v1/settings/profiles*`
- [x] T031 [P] [US3] Add UI permission gate tests for read-only profiles and blocked models in `apps/migration-ui/`

### Implementation for User Story 3

- [x] T032 [US3] Apply `require_capability(..., "can_manage_settings")` on profile create/update/delete only in `services/accelerator_api/main.py`; allow operator GET
- [x] T033 [US3] Enforce `can_manage_models` on LLM mutation routes in `services/accelerator_api/main.py`
- [x] T034 [US3] Implement operator **read-only** profile list/detail UI with disabled mutation controls in `apps/migration-ui/src/app/settings/profiles/page.tsx`
- [x] T035 [P] [US3] Block operator access to `/settings/models` with explanation in `apps/migration-ui/src/app/settings/models/page.tsx`
- [x] T036 [US3] Add role capability hints on Settings nav and Agent tab per `contracts/platform-rbac.md` (SC-007 proxy)
- [x] T037 [P] [US3] Extend `apps/migration-ui/src/lib/auth.ts` types for new permission flags from session payload

**Checkpoint**: Operator read-only profiles + 100% mutation block (SC-002, FR-010)

---

## Phase 6: User Story 4 — Concurrent Sessions in Docker (Priority: P2)

**Goal**: Admin, operator, and approver maintain independent sessions; audit attributes correct user.

**Independent Test**: Two+ browsers on prod overlay → parallel login → actions attributed in audit (SC-005, FR-011).

### Tests for User Story 4

- [x] T038 [P] [US4] Create `tests/test_concurrent_auth_sessions.py` — two tokens active; logout one does not invalidate other
- [x] T039 [P] [US4] Add audit attribution tests — agent/approval events include username and role, not `local-developer`

### Implementation for User Story 4

- [x] T040 [US4] Ensure `credentials: 'include'` on all agent and accelerator fetches in `apps/migration-ui/src/lib/agent.ts` and `apps/migration-ui/src/lib/api.ts`
- [x] T041 [US4] Forward session cookie to accelerator on agent internal httpx calls in `services/agent/main.py`
- [x] T042 [US4] Update `IdeAuditBridge.record()` in `ado2gh/agents/local/audit_bridge.py` to use authenticated username and role (FR-012)

**Checkpoint**: Concurrent sessions without cross-user bleed (SC-005)

---

## Phase 7: User Story 5 — Unified Live Approval Workflow (Priority: P2)

**Goal**: Operator request-live (Agent, dashboard migrate, pipeline) enqueues unified store; admin **or approver** approves/denies; dual AND gates; no silent live mutations.

**Independent Test**: Operator dry-run → request live → admin/approver approves from `/settings/approvals` → live execution runs (SC-003).

### Tests for User Story 5

- [x] T043 [P] [US5] Extend `tests/test_live_approval_queue.py` — approve/deny/resume for `agent_session`, `migrate_job`, `pipeline_run`; idempotent approve
- [x] T044 [P] [US5] Add contract tests for `/v1/platform/approvals*` in `tests/contract/test_004_agent_pev_contracts.py`
- [x] T045 [P] [US5] Extend `tests/test_agent_pev_live_gate.py` — platform approval then `enforce_live_gate()` AND failure path (FR-014)

### Implementation for User Story 5

- [x] T046 [US5] Implement `GET/POST /v1/platform/approvals` and approve/deny routes in `services/accelerator_api/main.py` per `contracts/live-approval-api.md`
- [x] T047 [US5] In `ado2gh/api/live_approval_store.py` `approve()` — call `enforce_live_gate()` when `assignment_id` set before any resume (all scope types)
- [x] T048 [US5] Delegate agent `POST /v1/sessions/{id}/request-live` to unified store in `services/agent/main.py` (`scope_type=agent_session`)
- [x] T049 [US5] Enqueue unified approval on operator live `POST /v1/migrate` in `services/accelerator_api/main.py` (`scope_type=migrate_job`) before live execution
- [x] T050 [US5] Enqueue unified approval on operator pipeline live start in `ado2gh/api/pipeline_runner.py` or `services/accelerator_api/main.py` (`scope_type=pipeline_run`)
- [x] T051 [US5] On platform approve, resume scoped action: agent session live PEV, migrate job, or pipeline run per `scope_type`
- [x] T052 [US5] Create approval queue page for admin **and approver** at `apps/migration-ui/src/app/settings/approvals/page.tsx`
- [x] T053 [US5] Wire operator request-live and approval status polling in `apps/migration-ui/src/components/AgentChat.tsx`
- [x] T054 [US5] Add platform approval API client helpers in `apps/migration-ui/src/lib/api.ts`
- [x] T055 [US5] Deprecate in-memory `_approvals` in `services/agent/main.py` — proxy to StateDB with `DeprecationWarning` per constitution III

**Checkpoint**: 100% operator live requests blocked until admin or approver records approval (SC-003, FR-008/009)

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Docstrings, coverage, quickstart validation

- [x] T056 [P] Add docstrings to all new public functions/classes in `ado2gh/api/platform_rbac.py`, `live_approval_store.py`, and LLM adapters per constitution Principle II
- [x] T057 [P] Verify zero secrets in audit payloads in `tests/test_llm_model_runtime.py` and approval audit tests (SC-006)
- [x] T058 Run all scenarios in `specs/004-agent-pev-rbac/quickstart.md` including approver-only approval and dual-provider onboarding
- [x] T059 Confirm ≥85% coverage on changed modules via `pytest --cov=ado2gh --cov=services/agent --cov=services/accelerator_api`
- [x] T060 [P] Add Settings nav link to `/settings/approvals` for users with `can_approve_live_execution` in migration-ui layout

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Blocks all user stories
- **User Stories**: After Phase 2 — US1 MVP first; US2 parallel with US1; US3 after/overlap US2 guards; **US5 requires T010 + T015** before live resume paths
- **Polish (Phase 8)**: After US1–US5 as needed for release

### User Story Dependencies

| Story | Depends on | Notes |
|-------|------------|-------|
| US1 PEV Tab | Phase 2 | MVP |
| US2 LLM Models | Phase 2 | Both providers required for SC-004 |
| US3 RBAC | Phase 2 | Read-only GET + mutation guards |
| US4 Concurrent | Phase 2, US1 cookies | Audit crosses all stories |
| US5 Unified queue | Phase 2, US1 T015, T010 | T047 dual gate before T051 resume |

### Parallel Opportunities

- Phase 1: T002, T003
- Phase 2: T004+T005; T008+T009
- After Phase 2: US1 (T013–T020) ∥ US2 (T021–T029)
- US3 UI (T034–T035) ∥ US3 API (T032–T033)
- US5 enqueue tasks T049–T050 ∥ US5 UI T052–T054 after T046

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 + 2
2. Phase 3 (US1)
3. Validate quickstart Scenario 3
4. Stop before live approval if demoing dry-run only

### Full Feature Delivery

1. US1 → US2 → US3 → US5 → US4 → Polish
2. Validate quickstart Scenarios 1–9 + SC-001 through SC-006

### Suggested MVP Scope

**User Story 1** (Phases 1–3): dry-run PEV Agent tab with remediation.

---

## Notes

- Partial scaffolding exists — verify before re-implementing
- Approver role: approve queue only; no `can_operate` (see `contracts/platform-rbac.md`)
- Assignment-scoped RBAC unchanged (FR-014)
- Never log or return raw API keys after save (CA-003)
