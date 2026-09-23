# Implementation Plan: LLM Model Catalog, Validation, and Connectivity Settings

**Branch**: `006-llm-model-catalog` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-llm-model-catalog/spec.md`

## Summary

Enhance the existing LLM model registry (feature `004`) so admins **pick models from a catalog** instead of typing model IDs, **validate connectivity** before enable/default, and configure **environment-level proxy + custom CA** for corporate networks. Add **local/self-hosted** provider support (Ollama-compatible discovery) with optional manual model-ID override gated by an environment toggle (default off).

Extends `LLMModelStore`, adds `ConnectivityStore` + `ModelCatalogService` + `ModelValidationService`, new accelerator routes, `OllamaProvider` / proxy-aware httpx client in `llm_provider.py`, and refactors `apps/migration-ui` Models + new Connectivity settings pages.

## Technical Context

**Language/Version**: Python >= 3.9 (`ado2gh`, `services/accelerator_api`, `services/agent`); TypeScript / Next.js 14 (`apps/migration-ui`)

**Primary Dependencies**: FastAPI; httpx (proxy + custom CA via `verify=` / SSL context); existing `LLMModelStore`, `platform_rbac`, audit bridge; React Query UI

**Storage**: `{ADO2GH_DATA_DIR}/llm_models.json` (+ `_secrets` sidecar, extended fields); `{ADO2GH_DATA_DIR}/connectivity_profile.json` (+ `_secrets` for proxy password + custom CA PEM); bundled preset catalog JSON in `ado2gh/api/data/llm_presets.json`

**Testing**: pytest — catalog service, validation categories, connectivity store redaction, proxy/CA wiring (mocked httpx), UI permission tests (vitest); contract tests for new endpoints; **≥85%** on changed `ado2gh/api/*`, `ado2gh/agents/llm_provider.py`

**Target Platform**: Docker Compose (dev + prod overlay); auth enabled for RBAC paths

**Performance Goals**: Validation and catalog fetch return pass/fail within **30s** p95 (SC-002); single-flight per draft/model id (FR-013)

**Constraints**: CA-003 secret/CA redaction; CA-004 audit on model + connectivity changes + validation outcomes; FR-011 validation required before enable/default; admin-only model/connectivity mutations (FR-012)

**Scale/Scope**: Single-tenant; preset + live catalog for OpenAI + Anthropic; Ollama-style local discovery; no Vault/Postgres migration in v1

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Separate `catalog`, `validation`, `connectivity` modules; thin FastAPI routes |
| II. Documentation | Docstrings on catalog fetch, validation, connectivity load/save |
| III. Deprecation | Keep manual `model_id` in API for compat; UI catalog-first; document override toggle |
| IV. Architecture & Naming | Domain modules under `ado2gh/api/`; settings routes grouped under `/v1/settings/` |
| V. Enterprise Safeguards | Validation gate before enable; secrets/CA never logged; audit validation pass/fail |
| VI. Testing (85%+) | Unit + contract tests for catalog, validation, connectivity; UI vitest for picker gates |

**Result**: [x] PASS

**Post-design re-check**: [x] PASS

## Project Structure

### Documentation (this feature)

```text
specs/006-llm-model-catalog/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── llm-catalog-api.md
│   ├── connectivity-api.md
│   └── llm-models-ui.md
└── tasks.md                    # /speckit-tasks (not created here)
```

### Source Code (repository root)

```text
ado2gh/
├── api/
│   ├── llm_model_store.py          # validation_status, catalog fields, enable gate
│   ├── connectivity_store.py       # NEW — proxy, custom CA, allow_custom_model_id
│   ├── model_catalog.py            # NEW — preset + live + ollama discovery
│   ├── model_validation.py         # NEW — validate draft/saved model
│   └── data/
│       └── llm_presets.json        # NEW — curated OpenAI/Anthropic presets
├── agents/
│   └── llm_provider.py             # OllamaProvider; proxy/CA httpx helper

services/accelerator_api/
└── main.py                         # catalog, validate, connectivity routes

apps/migration-ui/src/
├── app/settings/models/page.tsx    # catalog picker, Validate, enable gate
├── app/settings/connectivity/page.tsx  # NEW — proxy, CA, override toggle
├── lib/permissions.ts              # connectivity tab (admin only)
└── lib/llmSettings.ts              # NEW — API helpers

tests/
├── test_model_catalog.py
├── test_model_validation.py
├── test_connectivity_store.py
├── contract/test_006_llm_catalog_contracts.py
└── apps/migration-ui/src/lib/llmSettings.test.ts
```

**Structure Decision**: Extend existing monorepo layout from `004`; no new services. Catalog/validation run in accelerator API (same origin as UI cookie auth).

## Complexity Tracking

No violations.

## Phase 0 & Phase 1 Artifacts

| Artifact | Path | Status |
|----------|------|--------|
| Research | [research.md](./research.md) | Complete |
| Data model | [data-model.md](./data-model.md) | Complete |
| Catalog API | [contracts/llm-catalog-api.md](./contracts/llm-catalog-api.md) | Complete |
| Connectivity API | [contracts/connectivity-api.md](./contracts/connectivity-api.md) | Complete |
| Models UI | [contracts/llm-models-ui.md](./contracts/llm-models-ui.md) | Complete |
| Quickstart | [quickstart.md](./quickstart.md) | Complete |

## Implementation Notes (for `/speckit-tasks`)

1. **Connectivity tab** at `/settings/connectivity` (admin only): proxy host/port/auth, custom CA paste/upload, `allow_custom_model_id` toggle (default false). Changing proxy or CA invalidates validation on **all** registered models. **Read-only `GET /v1/settings/connectivity`** ships in foundational phase (Phase 2) so Models page can read override toggle before Connectivity UI.
2. **Catalog API** `GET /v1/settings/llm-models/catalog?provider=openai&api_key=...` — live primary, preset fallback with `source` + `stale` flag; **single-flight** per catalog key (FR-013).
3. **Local discovery** `GET /v1/settings/llm-models/catalog?provider=ollama&base_url=http://host:11434` — proxies **not** applied to local base URLs.
4. **Validate** `POST /v1/settings/llm-models/validate` (draft body) and `POST /v1/settings/llm-models/{id}/validate` — returns `{ status, category, message, validated_at }`; 30s timeout.
5. **Enable gate** — `POST/PUT` reject `enabled: true` or `default_for_agent: true` unless `validation_status === passed`; UI disables Enable/Default until Validate succeeds.
6. **Model form** — replace free-text Model ID with searchable catalog `<select>`; show override field only when `allow_custom_model_id` and catalog empty/failed.
7. **Ollama runtime** — new `provider: ollama` with `base_url` on model record; `OllamaProvider` uses `/api/chat` or OpenAI-compatible `/v1/chat/completions`.
8. **httpx client factory** — `ado2gh/api/http_llm.py` builds client with env connectivity proxy + custom CA for cloud calls only.
9. **Audit** — `llm.model.validated`, `connectivity.updated`, `llm.model.enabled` (no secrets).
10. **Presets** — ship `llm_presets.json`; document out-of-band update process in quickstart.
