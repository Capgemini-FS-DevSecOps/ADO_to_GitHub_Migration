# Tasks: Cloud-Hosted LLM Credential Detection and Admin Approval

**Input**: Design documents from `specs/007-cloud-llm-credentials/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% line coverage on new/changed modules per `pyproject.toml` pattern)

**Organization**: Tasks grouped by user story for independent delivery and validation.

**Remediation applied (2026-06-20)**: Analyze polish — `platform-supplied-llm.md`, `GET /v1/agent/models`, SC-001 all hyperscalers, `--cov` for `llm_provider`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Contract scaffolding, coverage scope, env contract documentation

- [X]  Confirm `.specify/feature.json` points to `specs/007-cloud-llm-credentials`
- [X]  [P] Create `tests/contract/test_007_cloud_credentials_contracts.py` listing paths from `specs/007-cloud-llm-credentials/contracts/` (incl. `agent-models-api.md`, `platform-supplied-llm.md`)
- [X]  [P] Extend `pyproject.toml` `[tool.pytest.ini_options]` `addopts` `--cov` entries: `ado2gh.api.cloud_credentials_store`, `ado2gh.api.cloud_credential_detector`, `ado2gh.api.cloud_credential_probe`, `ado2gh.api.platform_managed_model`, `ado2gh.agents.llm_provider` (match existing `ado2gh.api.llm_model_store` pattern)
- [X]  [P] Document platform-supplied model env vars in `.env.example` per `specs/007-cloud-llm-credentials/contracts/platform-supplied-llm.md` (model ID/region only — no mode flag)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Credential store with FR-010 snapshot diff, model store extensions, audit event names — **blocks all user stories**

**⚠️ CRITICAL**: No user story work until this phase is complete

### Tests (Foundational)

- [X]  [P] Create `tests/test_cloud_credentials_store.py` — approval FSM, FR-010 snapshot diff in `apply_scan()` → pending, no secret fields in `to_public()` (fail before implementation)
- [X]  [P] Extend `tests/test_llm_model_store_006.py` or add `tests/test_llm_model_store_007.py` — `credential_mode`, `platform_supplied`, ambient enable gate, block enable when source rejected (G2) (fail first)

### Implementation (Foundational)

- [X]  Create `ado2gh/api/cloud_credentials_store.py` — load/save `{ADO2GH_DATA_DIR}/cloud_credentials.json`, `CloudCredentialSource` FSM, **`apply_scan()` with FR-010 snapshot compare** per `data-model.md`
- [X]  Extend `ado2gh/api/llm_model_store.py` — add `credential_mode` (`byok`|`ambient`), `platform_supplied`, `ambient_provider`; invalidate ambient models on source revoke/pending
- [X]  Add audit helpers for `cloud_credentials.approved`, `.rejected`, `.revoked`, `.scanned` in existing audit bridge used by `services/accelerator_api/main.py`
- [X]  Add `require_manage_models(request)` as first line of each cloud-credentials route stub in `services/accelerator_api/main.py` per `ado2gh/api/platform_rbac.py` (same pattern as `006` LLM routes)

**Checkpoint**: Store + model extensions + audit hooks + guard pattern ready; FR-010 in `apply_scan` from day one

---

## Phase 3: User Story 1 — Discover Cloud LLM Credential Sources (Priority: P1) 🎯 MVP

**Goal**: Admin sees presence-detected **AWS, Microsoft Foundry, and GCP Vertex** sources (pending, non-secret summary) on Settings visit or scan.

**Independent Test**: Deploy with AWS env/role → open Cloud credentials → AWS card `pending`, method + region, no secrets. Repeat for Microsoft Foundry and Vertex hosts (spec US1; quickstart step 1).

### Tests for User Story 1

- [X]  [P] [US1] Create `tests/test_cloud_credential_detector.py` — **AWS, Microsoft Foundry, GCP Vertex** presence signals, incomplete `missing_fields`, no API calls during scan
- [X]  [P] [US1] Add `GET /v1/settings/cloud-credentials` contract tests in `tests/contract/test_007_cloud_credentials_contracts.py`
- [X]  [P] [US1] Add operator-role **403** contract tests for all cloud-credentials routes in `tests/contract/test_007_cloud_credentials_contracts.py` (G3)
- [X]  [P] [US1] Add **SC-003 secret redaction** contract tests per `contracts/cloud-credentials-api.md#secret-redaction` — fake env secrets never appear in JSON or audit payloads (G1)

### Implementation for User Story 1

- [X]  [US1] Create `ado2gh/api/cloud_credential_detector.py` — `scan_presence()` for **aws, foundry, gcp** per `research.md`; primary_method priority; snapshot for FR-010
- [X]  [US1] Wire detector into `CloudCredentialsStore.apply_scan()` in `ado2gh/api/cloud_credentials_store.py`
- [X]  [US1] Add `GET /v1/settings/cloud-credentials` and `POST /v1/settings/cloud-credentials/scan` in `services/accelerator_api/main.py` with `require_manage_models(request)` per `contracts/cloud-credentials-api.md`
- [X]  [P] [US1] Create `apps/migration-ui/src/lib/cloudCredentials.ts` — `fetchCloudCredentials`, `scanCloudCredentials`, `updateCloudCredential`, types
- [X]  [US1] Create `apps/migration-ui/src/app/settings/cloud-credentials/page.tsx` — source cards for all three providers, status badges, empty state, Rescan button, **incomplete-field editor** (FR-002a / U1)
- [X]  [P] [US1] Add Cloud credentials tab in `apps/migration-ui/src/lib/permissions.ts` and extend `apps/migration-ui/src/lib/permissions.test.ts`

**Checkpoint**: Admin can scan and view all three hyperscaler sources; operators denied via UI and API 403

---

## Phase 4: User Story 2 — Admin Approves, Validates, and Enables (Priority: P1)

**Goal**: Approve/reject/revoke with live probe on approve; audit trail; **validate and enable** platform-supplied models using approved ambient credentials.

**Independent Test**: Pending AWS → complete fields if needed → Approve → probe pass → `approved` → Validate → Enable without API key (spec US2; quickstart steps 3–4).

### Tests for User Story 2

- [X]  [P] [US2] Create `tests/test_cloud_credential_probe.py` — **Bedrock, Microsoft Foundry, Vertex** probe mocks, failure categories, incomplete source → 400 on approve (U1)
- [X]  [P] [US2] Add approve/reject/revoke/scan/PATCH contract tests + audit event assertions in `tests/contract/test_007_cloud_credentials_contracts.py` (G5, SC-002)
- [X]  [P] [US2] Extend `tests/test_model_validation.py` — ambient validation paths for bedrock, foundry, vertex (US2 owns validation)
- [X]  [P] [US2] Create `tests/test_platform_managed_model.py` — env sync, read-only model record, `enabled` false until validated
- [X]  [P] [US2] Add BYOK regression test: empty cloud detection → BYOK model save/validate succeeds in `tests/test_model_validation.py` (SC-004 / FR-013)

### Implementation for User Story 2

- [X]  [US2] Create `ado2gh/api/cloud_credential_probe.py` — `probe_provider(provider)` for **aws, foundry, gcp** (no stubs in v1)
- [X]  [US2] Add `POST .../{provider}/approve`, `/reject`, `/revoke` and `PATCH .../{provider}` (U1 forbidden-field validation) routes in `services/accelerator_api/main.py`
- [X]  [US2] Implement `get_ambient_credentials(provider)` guard in `ado2gh/api/cloud_credentials_store.py` — raises if not approved (FR-005)
- [X]  [US2] Create `ado2gh/api/platform_managed_model.py` — `sync_on_startup()`, `read_config_from_env()`, upsert `LLMModelStore` (`platform_supplied`; availability from approval state)
- [X]  [P] [US2] Add `bedrock`, `foundry`, `vertex` to `ado2gh/api/llm_provider_registry.py` and presets in `ado2gh/api/data/llm_presets.json`
- [X]  [US2] Extend `ado2gh/api/model_validation.py` for ambient validation on all three providers; block enable when ambient source rejected (G2)
- [X]  [US2] Call `platform_managed_model.sync_on_startup()` from `services/accelerator_api/main.py` lifespan/startup
- [X]  [US2] Add `GET /v1/settings/cloud-credentials/platform-model` in `services/accelerator_api/main.py`
- [X]  [US2] Add Approve/Reject/Revoke + probe error banners + Validate/Enable CTA in `apps/migration-ui/src/app/settings/cloud-credentials/page.tsx` and `apps/migration-ui/src/app/settings/models/page.tsx`
- [X]  [P] [US2] Add vitest tests for `cloudCredentials.ts` API helpers in `apps/migration-ui/src/lib/cloudCredentials.test.ts`

**Checkpoint**: Full approve → probe → audit → validate → enable workflow for all three hyperscalers; BYOK unaffected (FR-006, FR-013)

---

## Phase 5: User Story 3 — Use Approved Cloud Credentials in Agent Sessions (Priority: P2)

**Goal**: Agent API exposes approved cloud-backed models as selectable options; operators use them in sessions.

**Independent Test**: Approve + validate AWS model → `GET /v1/agent/models` lists Bedrock option → operator selects and chats (spec US3; `contracts/agent-models-api.md`).

### Tests for User Story 3

- [X]  [P] [US3] Extend `tests/test_llm_provider.py` or add `tests/test_bedrock_provider.py` — `BedrockProvider` + Foundry/Vertex providers with mocked SDKs
- [X]  [P] [US3] Add agent API contract tests for `GET /v1/agent/models` per `contracts/agent-models-api.md` (FR-016, U2)
- [X]  [P] [US3] Add agent API test: demoted pending source removes ambient model from available list

### Implementation for User Story 3

- [X]  [US3] Implement `BedrockProvider`, `FoundryProvider`, `VertexProvider` in `ado2gh/agents/llm_provider.py` and wire `build_provider_from_config` + `get_llm_provider` ambient path
- [X]  [US3] Add `GET /v1/agent/models` in `services/agent/main.py` per `contracts/agent-models-api.md` — cloud-backed when source **approved** + model **enabled**
- [X]  [US3] Call `platform_managed_model.sync_on_startup()` from `services/agent/main.py` lifespan/startup
- [X]  [US3] Update `apps/migration-ui/src/components/AgentChat.tsx` to read `GET /v1/agent/models` for picker options (U2)

**Checkpoint**: End-to-end agent inference with admin-approved ambient credentials as selectable options

---

## Phase 6: User Story 4 — Rescan After Deployment Changes (Priority: P3)

**Goal**: Rescan updates sources within 2–5s; material config change invalidates approval and ambient model validation (FR-010 logic in T007; this phase adds UX + guards).

**Independent Test**: Change `AWS_REGION` → Rescan → AWS returns `pending` within 2–5s (spec US4; quickstart step 6).

### Tests for User Story 4

- [X]  [P] [US4] Extend `tests/test_cloud_credentials_store.py` — rescan demotes `approved` → `pending` and invalidates ambient model `validation_status` (model invalidation only; snapshot diff covered in T005)
- [X]  [P] [US4] Add single-flight / concurrent scan test + **2–5s completion assertion** for `POST /scan` in `tests/contract/test_007_cloud_credentials_contracts.py` (A1)

### Implementation for User Story 4

- [X]  [US4] Add single-flight guard for scan in `services/accelerator_api/main.py`
- [X]  [US4] Wire global Rescan UX + pending re-approval messaging in `apps/migration-ui/src/app/settings/cloud-credentials/page.tsx`

**Checkpoint**: Rescan safely refreshes state without redeploy; performance target documented

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Docs, quickstart validation, coverage gate

- [X]  [P] Add optional `boto3` and Azure/GCP client deps to `[project.optional-dependencies] api` in `pyproject.toml` if not already available
- [X]  Run `specs/007-cloud-llm-credentials/quickstart.md` validation on Docker stack for all three providers; fix gaps
- [X]  [P] Add EC2/Docker pointer in `docs/LOCAL_DEVELOPMENT.md` linking to quickstart
- [X]  Verify ≥85% coverage on new modules via `pytest` and adjust tests in `tests/test_cloud_*.py` and `tests/test_platform_managed_model.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **blocks all user stories**
- **US1 (Phase 3)**: Depends on Foundational — **MVP list/scan**
- **US2 (Phase 4)**: Depends on US1
- **US3 (Phase 5)**: Depends on US2
- **US4 (Phase 6)**: Depends on US1 store/detector; integrates with US2 invalidation
- **Polish (Phase 7)**: After US1–US3 minimum

### User Story Dependencies

| Story | Depends on | Independent test |
|-------|------------|------------------|
| US1 P1 | Foundational | Scan + list all 3 hyperscalers |
| US2 P1 | US1 | Approve + probe + audit + validate + enable |
| US3 P2 | US2 | `GET /v1/agent/models` + inference |
| US4 P3 | US1, US2 | Rescan invalidates approval in 2–5s |

### Parallel Opportunities

- **Phase 1**: T002, T003, T004 in parallel
- **Phase 2**: T005, T006 in parallel; then T007–T010 sequential
- **US1**: T011–T014 parallel; T018, T020 parallel after T017
- **US2**: T021–T025 parallel; T030 parallel with T029
- **US3**: T036–T038 parallel
- **US4**: T043–T044 parallel
- **Polish**: T047, T049 parallel

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1–2
2. Complete Phase 3 (US1) — detect + display all three hyperscalers
3. Complete Phase 4 (US2) — approve + validate + enable
4. **STOP and VALIDATE**: quickstart steps 1–4 on EC2/Docker

### Suggested MVP scope

**Phases 1–4 (T001–T035)** delivers hyperscaler onboarding per platform team requirements.

---

## Notes

- Presence scan MUST NOT call cloud APIs (FR-001)
- Secret redaction: `contracts/cloud-credentials-api.md#secret-redaction` (G1 / SC-003)
- Platform-supplied model SKU is read-only; env contract in `contracts/platform-supplied-llm.md`
- **Runtime availability** = approved source + enabled model (internal state), not env mode flag
- Microsoft Foundry is the official Azure hyperscaler name (F1)
- Agent picker: `GET /v1/agent/models` per `contracts/agent-models-api.md` (U2)
- PATCH forbidden secret fields per cloud-credentials API (U1)
