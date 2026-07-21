# Tasks: LLM Model Catalog, Validation, and Connectivity Settings

**Input**: Design documents from `specs/006-llm-model-catalog/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% line coverage on changed `ado2gh/api/*`, `ado2gh/agents/llm_provider.py`)

**Organization**: Tasks grouped by user story for independent delivery and validation.

**Clarifications applied**: Global env proxy; live catalog primary; validation required before enable/default; custom CA v1; override toggle default off. **Analyze remediation (2026-06-16)**: I1 early connectivity GET; I2 catalog single-flight; I3 FR-005 `tls`; I4 `llm.model.enabled` audit; I5 SC-002 concurrent validate; I7 T022 uses `http_llm`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Contract scaffolding, presets bundle, coverage scope

- [x] T001 Confirm `.specify/feature.json` points to `specs/006-llm-model-catalog`
- [x] T002 [P] Create `tests/contract/test_006_llm_catalog_contracts.py` listing paths from `specs/006-llm-model-catalog/contracts/`
- [x] T003 [P] Extend `pyproject.toml` `--cov` entries for `ado2gh.api.connectivity_store`, `model_catalog`, `model_validation`, `http_llm`
- [x] T004 [P] Add bundled presets in `ado2gh/api/data/llm_presets.json` (OpenAI + Anthropic per `research.md` R8)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Connectivity store, HTTP client factory, extended model store — **blocks all user stories**

**⚠️ CRITICAL**: No user story work until this phase is complete

### Tests (Foundational)

- [x] T005 [P] Create `tests/test_connectivity_store.py` — proxy/CA redaction, defaults, `allow_custom_model_id` false (fail before implementation)
- [x] T006 [P] Extend `tests/test_llm_model_runtime.py` or add `tests/test_llm_model_store_006.py` — validation fields, enable gate rejects `enabled` without `passed` (fail first)

### Implementation (Foundational)

- [x] T007 Create `ado2gh/api/connectivity_store.py` — load/save `{ADO2GH_DATA_DIR}/connectivity_profile.json` + `_secrets` sidecar per `data-model.md`
- [x] T008 Create `ado2gh/api/http_llm.py` — `build_llm_http_client(*, for_cloud: bool)` with env proxy + custom CA SSL context (30s default timeout)
- [x] T009 Extend `ado2gh/api/llm_model_store.py` — add `validation_status`, `validation_at`, `validation_category`, `validation_message`, `catalog_source`, `catalog_label`, `base_url`; reject `enabled`/`default_for_agent` unless `validation_status=passed`
- [x] T010 Add `invalidate_all_model_validations()` in `ado2gh/api/llm_model_store.py` and call from `ConnectivityStore.save()` when proxy/CA/override toggle changes
- [x] T011 Extend `GET /v1/settings/llm-models` in `services/accelerator_api/main.py` to return new public validation + catalog fields per `contracts/llm-catalog-api.md`
- [x] T011a Add read-only `GET /v1/settings/connectivity` (masked secrets, incl. `allow_custom_model_id`) in `services/accelerator_api/main.py` — **I1**: Models page reads override toggle before Connectivity settings UI (US3)

**Checkpoint**: Stores, HTTP client, and read-only connectivity GET ready; models list exposes validation metadata

---

## Phase 3: User Story 1 — Select a Model From a Catalog (Priority: P1) 🎯 MVP

**Goal**: Admin picks provider + catalog model without typing model ID; live primary, preset fallback with stale notice.

**Independent Test**: Admin selects OpenAI model from dropdown → saves → Agent picker shows display name (SC-001 catalog portion; quickstart scenario 1 steps 3–5).

### Tests for User Story 1

- [x] T012 [P] [US1] Create `tests/test_model_catalog.py` — OpenAI live mock, preset fallback + `stale: true`, Anthropic preset-only, **catalog single-flight** concurrent requests (FR-013)
- [x] T013 [P] [US1] Add `GET /v1/settings/llm-models/catalog` contract tests in `tests/contract/test_006_llm_catalog_contracts.py`

### Implementation for User Story 1

- [x] T014 [US1] Create `ado2gh/api/model_catalog.py` — `list_catalog(provider, api_key, base_url)` using presets + live OpenAI fetch via `http_llm`; in-process single-flight lock per catalog key (FR-013)
- [x] T015 [US1] Add `GET /v1/settings/llm-models/catalog` route with `require_manage_models` and single-flight guard in `services/accelerator_api/main.py`
- [x] T016 [P] [US1] Create `apps/migration-ui/src/lib/llmSettings.ts` — `fetchCatalog`, `fetchConnectivity` (read-only), `CatalogEntry` types
- [x] T017 [US1] Replace free-text Model ID with searchable catalog select + stale banner in `apps/migration-ui/src/app/settings/models/page.tsx`
- [x] T018 [US1] Persist `catalog_source`, `catalog_label`, and auto `model_id` from selection on create/update in `apps/migration-ui/src/app/settings/models/page.tsx`
- [x] T019 [P] [US1] Add vitest tests for catalog helpers in `apps/migration-ui/src/lib/llmSettings.test.ts`

**Checkpoint**: Cloud model onboarding via catalog picker without manual model ID (FR-001, FR-002, FR-003)

---

## Phase 4: User Story 2 — Validate Model Connection Before Use (Priority: P1)

**Goal**: Validate button proves connectivity; enable/default blocked until validation passes; categorized failures.

**Independent Test**: Validate cloud model → Passed → Enable enabled; without validate Enable disabled (quickstart scenario 5).

### Tests for User Story 2

- [x] T020 [P] [US2] Create `tests/test_model_validation.py` — pass/fail categories, **30s timeout (SC-002)**, **concurrent duplicate validate requests complete without hang** (single-flight or in-flight reuse; second call returns promptly), single-flight, no secrets in messages (SC-003)
- [x] T021 [P] [US2] Add validate endpoint contract tests in `tests/contract/test_006_llm_catalog_contracts.py`

### Implementation for User Story 2

- [x] T022 [US2] Create `ado2gh/api/model_validation.py` — `validate_draft(body)` and `validate_saved(model_id)` with category mapping (FR-005); cloud providers use **`build_llm_http_client(for_cloud=True)`** from `ado2gh/api/http_llm.py` (I7); local/Ollama uses direct client (`for_cloud=False`)
- [x] T023 [US2] Add `POST /v1/settings/llm-models/validate` and `POST /v1/settings/llm-models/{model_id}/validate` in `services/accelerator_api/main.py`
- [x] T024 [US2] Reset `validation_status` to `never_validated` on credential/catalog/provider/base_url change in `ado2gh/api/llm_model_store.py` upsert (FR-006)
- [x] T025 [US2] Emit audit `llm.model.validated` via `ado2gh/api/profile_governance.py` or audit bridge — status + category only (CA-004)
- [x] T025a [US2] Emit audit **`llm.model.enabled`** on enable or `default_for_agent` change when `validation_status=passed` — model id + flags only, no secrets (FR-014, I4)
- [x] T026 [US2] Add Validate button, status badge, disabled Enable/Default controls in `apps/migration-ui/src/app/settings/models/page.tsx` (FR-011)
- [x] T027 [P] [US2] Add `validateModel` / `validateSavedModel` helpers in `apps/migration-ui/src/lib/llmSettings.ts`

**Checkpoint**: Full cloud onboarding with validate-then-enable in under 3 minutes (SC-001, SC-002)

---

## Phase 5: User Story 3 — Corporate Proxy and Network Settings (Priority: P2)

**Goal**: Environment-level proxy + custom CA self-service; cloud catalog/validation use connectivity profile.

**Independent Test**: Configure proxy + CA in Settings → Validate cloud model succeeds through corporate path (quickstart scenario 3, SC-006).

### Tests for User Story 3

- [x] T028 [P] [US3] Extend `tests/test_connectivity_store.py` — CA PEM storage, bulk model validation invalidation on update
- [x] T029 [P] [US3] Add connectivity API contract tests in `tests/contract/test_006_llm_catalog_contracts.py`
- [x] T030 [P] [US3] Mock proxy/CA applied in `tests/test_model_catalog.py` and `tests/test_model_validation.py` cloud paths

### Implementation for User Story 3

- [x] T031 [US3] Add `PUT /v1/settings/connectivity` and optional `POST /v1/settings/connectivity/test` in `services/accelerator_api/main.py` per `contracts/connectivity-api.md` (GET read-only delivered in T011a)
- [x] T032 [US3] Emit audit `connectivity.updated` on PUT without secret values (FR-014, CA-003)
- [x] T033 [US3] Create `apps/migration-ui/src/app/settings/connectivity/page.tsx` — proxy, CA paste, override toggle
- [x] T034 [P] [US3] Add Connectivity tab (admin only) in `apps/migration-ui/src/lib/permissions.ts` and verify in `apps/migration-ui/src/lib/permissions.test.ts`
- [x] T035 [US3] Show post-save notice “Re-validate models before enabling” in `apps/migration-ui/src/app/settings/connectivity/page.tsx`
- [x] T036 [US3] Wire `updateConnectivity` in `apps/migration-ui/src/lib/llmSettings.ts` (`fetchConnectivity` from T016)

**Checkpoint**: Proxy + custom CA configurable in UI without host env edits (FR-007, FR-007a, FR-008)

---

## Phase 6: User Story 4 — Local and Self-Hosted Models (Priority: P2)

**Goal**: Ollama-style discovery, validation, runtime inference; optional manual override when toggle enabled.

**Independent Test**: Register local Ollama model → Validate → Enable → Agent dry-run uses local provider (SC-004, quickstart scenario 4).

### Tests for User Story 4

- [x] T037 [P] [US4] Extend `tests/test_model_catalog.py` — Ollama `/api/tags` discovery, proxy not applied (`for_cloud=False`)
- [x] T038 [P] [US4] Extend `tests/test_llm_model_runtime.py` — `OllamaProvider` + `get_llm_provider` for `provider=ollama`
- [x] T039 [P] [US4] Add override gate tests — reject `catalog_source=override` when `allow_custom_model_id` false in `tests/test_llm_model_store_006.py`

### Implementation for User Story 4

- [x] T040 [US4] Add Ollama discovery branch in `ado2gh/api/model_catalog.py` via `GET {base_url}/api/tags`
- [x] T041 [US4] Implement `OllamaProvider` in `ado2gh/agents/llm_provider.py` and resolve in `_provider_from_model_config`
- [x] T042 [US4] Use `build_llm_http_client(for_cloud=True)` for OpenAI/Anthropic in `ado2gh/agents/llm_provider.py` (validation service wired in T022)
- [x] T043 [US4] Add Ollama provider + base URL fields in `apps/migration-ui/src/app/settings/models/page.tsx`
- [x] T044 [US4] Show custom model ID override field when `fetchConnectivity().allow_custom_model_id` is true (FR-010; **depends on T011a + T016**, not US3 UI) in `apps/migration-ui/src/app/settings/models/page.tsx`
- [x] T045 [US4] Extend `POST/PUT /v1/settings/llm-models` to accept `base_url` and `catalog_source=override` when toggle on in `services/accelerator_api/main.py`

**Checkpoint**: Local model registered, validated, and usable in agent session (FR-009, SC-004)

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quickstart automation, CI, coverage, operator regression

- [x] T046 [P] Create `tests/test_006_quickstart_scenarios.py` covering quickstart scenarios 1, 2, 5, 7, 8
- [x] T047 [P] Extend `tests/test_platform_rbac.py` — operator `403` on catalog, validate, connectivity routes (FR-012)
- [x] T048 Verify ≥85% coverage on new modules via full `pytest` and adjust `pyproject.toml` omit list if needed
- [x] T049 [P] Extend `.github/workflows/ci.yml` `ui-permissions` job to run `llmSettings.test.ts` or add `ui-llm-settings` job
- [x] T050 Run quickstart scenarios 3–4 manually; document results in `specs/006-llm-model-catalog/quickstart-validation.md`
- [x] T051 [P] Verify zero secrets in validation, connectivity, and **`llm.model.enabled`** audit payloads in `tests/test_model_validation.py`
- [x] T052 Mark all tasks complete and sync `.specify/feature.json` after implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **blocks all user stories**
- **US1 (Phase 3)**: Depends on Foundational — MVP catalog picker
- **US2 (Phase 4)**: Depends on Foundational; integrates with US1 form but testable via API alone
- **US3 (Phase 5)**: Depends on Foundational; enhances US1/US2 cloud paths
- **US4 (Phase 6)**: Depends on Foundational + US1 catalog patterns; validation from US2
- **Polish (Phase 7)**: Depends on US1–US4 for full quickstart

### User Story Dependencies

| Story | Depends on | Independent test |
|-------|------------|------------------|
| US1 | Phase 2 | Catalog API + picker without validate |
| US2 | Phase 2 (+ US1 UI for full flow) | Validate API + enable gate |
| US3 | Phase 2 | Connectivity API + proxy mock tests |
| US4 | Phase 2 (T011a, T016 read-only connectivity) + US1 catalog + US2 validate | Ollama discovery + runtime; override UI uses early GET, not US3 tab |

### Parallel Opportunities

- T002, T003, T004 in Setup
- T005, T006 in Foundational tests
- T012, T013 parallel; T016, T019 parallel with backend after T015
- T020, T021 parallel
- T028–T030 parallel in US3 tests
- T037–T039 parallel in US4 tests
- T046, T047, T049, T051 parallel in Polish

### Parallel Example: User Story 1

```bash
# Tests first (parallel):
pytest tests/test_model_catalog.py tests/contract/test_006_llm_catalog_contracts.py -k catalog

# Then backend + UI (sequential core, parallel helpers):
# T014 model_catalog.py → T015 routes → T017 page.tsx
# T016 llmSettings.ts + T019 vitest in parallel once T015 exists
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1–2 (Setup + Foundational)
2. Complete Phase 3 (US1 catalog picker)
3. Complete Phase 4 (US2 validate + enable gate)
4. **STOP and VALIDATE** — quickstart scenario 1 + 5
5. Demo cloud model onboarding without manual model ID

### Incremental Delivery

1. Setup + Foundational → stores ready
2. US1 + US2 → MVP cloud catalog + validate (P1)
3. US3 → corporate proxy/CA (P2)
4. US4 → local Ollama (P2)
5. Polish → CI + quickstart sign-off

### Suggested MVP Scope

**Phases 1–4 only** (T001–T027): catalog selection + validation gate for cloud providers. Defer connectivity UI and Ollama to next increment.

---

## Notes

- Preserve 004 LLM CRUD routes; extend do not replace
- Local catalog requests MUST NOT use corporate proxy (`http_llm for_cloud=False`)
- Changing connectivity profile invalidates **all** model validations (plan note + data-model)
- Operator access unchanged: blocked from models/connectivity pages (FR-012)
