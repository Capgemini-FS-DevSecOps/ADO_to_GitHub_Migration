# Implementation Plan: Cloud-Hosted LLM Credential Detection and Admin Approval

**Branch**: `007-cloud-llm-credentials` | **Date**: 2026-06-18 | **Spec**: [spec.md](./spec.md) (updated 2026-06-16)

**Input**: Feature specification from `/specs/007-cloud-llm-credentials/spec.md`

## Summary

Add **ambient cloud LLM credential discovery** with **admin approval** for **all three hyperscalers** in v1: Amazon Bedrock (AWS), **Microsoft Foundry** (Azure), and Google Cloud Vertex AI on EC2/Docker deployments. Scan is **presence-only**; **live probes** run on Approve and model Validate. Platform team may supply fixed model SKUs via **deploy-time env vars**; **runtime availability** is driven by **internal approval state** (not a deployment mode env flag). **US2** owns approve → probe → audit → validate → enable; **US3** wires agent API model options.

Extends feature `006` with `CloudCredentialsStore`, `cloud_credential_detector`, `cloud_credential_probe`, `BedrockProvider` (+ Foundry/Vertex providers), `platform_managed_model` sync, accelerator routes, Settings **Cloud credentials** tab, and agent API **`GET /v1/agent/models`** with cloud-backed flags.

## Technical Context

**Language/Version**: Python >= 3.9 (`ado2gh`, `services/accelerator_api`, `services/agent`); TypeScript / Next.js 14 (`apps/migration-ui`)

**Primary Dependencies**: FastAPI; boto3 (Bedrock); httpx + Azure/GCP SDKs or REST for Foundry/Vertex probes; existing `LLMModelStore`, `model_validation`, `connectivity_store`, `platform_rbac`, audit bridge

**Storage**: `{ADO2GH_DATA_DIR}/cloud_credentials.json` (sources + approval metadata + admin-supplied non-secret fields, no secrets); extends `llm_models.json` with `credential_mode`, `platform_supplied`, `bedrock`/`foundry`/`vertex` providers

**Testing**: pytest — detector presence mocks (all 3 clouds), approval FSM, FR-010 snapshot diff in `apply_scan`, probe mocks, platform sync, BYOK regression, operator 403 contracts, audit event coverage; vitest for permissions + cloud credentials tab; **≥85%** on new modules including `ado2gh.agents.llm_provider` per `pyproject.toml` pattern

**Target Platform**: AWS EC2 + Docker Compose (primary); Azure/GCP hosts equally in v1 scope; local dev unchanged (stub/BYOK)

**Performance Goals**: Presence scan completes within **2–5 seconds** p95 (FR-009); live probe within **30s** (align with `006` validation)

**Constraints**: CA-002 admin approval before ambient use; CA-003 no secrets in API/audit/UI; CA-004 audit approve/reject/revoke/scan; FR-010 snapshot on every `apply_scan`; single-flight scan; `require_manage_models(request)` on all cloud-credentials routes (same as `006` LLM routes)

**Scale/Scope**: Single-tenant; max 3 cloud sources; **v1 fully implements AWS, Microsoft Foundry, and GCP** (presence + probe + validation)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (ado2gh v1.0.0)

| Principle | Gate (pass = compliant) |
|-----------|-------------------------|
| I. Clean Code | Separate `detector`, `probe`, `store`, `platform_managed_model` modules; thin routes |
| II. Documentation | Docstrings on detection signals, approval FSM, env contract |
| III. Deprecation | BYOK path unchanged; no breaking change to existing model APIs |
| IV. Architecture & Naming | `ado2gh/api/cloud_*` domain naming; routes under `/v1/settings/cloud-credentials` |
| V. Enterprise Safeguards | Approve-before-use; probe on approve; audit events; no secret persistence |
| VI. Testing (85%+) | Unit + contract + UI permission tests for approval FSM and redaction |

**Result**: [x] PASS

**Post-design re-check**: [x] PASS (2026-06-16 remediation)

## Project Structure

### Documentation (this feature)

```text
specs/007-cloud-llm-credentials/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cloud-credentials-api.md
│   ├── cloud-credentials-ui.md
│   ├── platform-supplied-llm.md
│   └── agent-models-api.md
└── tasks.md
```

### Source Code (repository root)

```text
ado2gh/
├── api/
│   ├── cloud_credentials_store.py    # NEW — JSON store, approval FSM, FR-010 in apply_scan
│   ├── cloud_credential_detector.py  # NEW — presence scan aws/foundry/gcp (v1 all three)
│   ├── cloud_credential_probe.py     # NEW — live probes on approve/validate (all three)
│   ├── platform_managed_model.py     # NEW — env sync → LLMModelStore
│   ├── llm_provider_registry.py      # ADD bedrock, foundry, vertex specs
│   ├── llm_model_store.py            # EXT credential_mode, platform_supplied, ambient gate
│   ├── model_validation.py           # EXT ambient paths (US2)
│   └── data/
│       └── llm_presets.json          # ADD hyperscaler presets

├── agents/
│   └── llm_provider.py               # ADD BedrockProvider + Foundry/Vertex providers

services/accelerator_api/
└── main.py                           # cloud-credentials routes; require_manage_models; startup sync

services/agent/
└── main.py                           # startup sync; GET /v1/agent/models with cloud-backed flags

apps/migration-ui/src/
├── app/settings/cloud-credentials/page.tsx  # NEW — incomplete field editor
├── lib/cloudCredentials.ts                  # NEW
├── lib/permissions.ts                       # cloud credentials tab
├── app/settings/models/page.tsx             # platform-supplied banner + validate CTA (US2)
└── components/AgentChat.tsx                 # reads agent API for model options (US3)

tests/
├── test_cloud_credential_detector.py
├── test_cloud_credentials_store.py
├── test_cloud_credential_probe.py
├── test_platform_managed_model.py
├── contract/test_007_cloud_credentials_contracts.py
└── apps/migration-ui/src/lib/permissions.test.ts

.env.example                            # document platform-supplied model env vars (not mode flag)
```

**Structure Decision**: Extend monorepo from `006`; no new services. Detection/probe in accelerator API; agent reads same stores via shared `ado2gh/api` modules.

## API Guard Pattern (A2)

All `/v1/settings/cloud-credentials/*` routes call `require_manage_models(request)` from `ado2gh.api.platform_rbac` as the **first line** of each handler—the same pattern used by feature `006` LLM settings routes in `services/accelerator_api/main.py`. Returns **403** with `Missing capability: can_manage_models` when the caller lacks admin capability.

## Complexity Tracking

No violations.

## Phase 0 & Phase 1 Artifacts

| Artifact | Path | Status |
|----------|------|--------|
| Research | [research.md](./research.md) | Complete (2026-06-19 — Microsoft Foundry, v1 all clouds) |
| Agent models API | [contracts/agent-models-api.md](./contracts/agent-models-api.md) | Complete |
| Data model | [data-model.md](./data-model.md) | Complete |
| Cloud credentials API | [contracts/cloud-credentials-api.md](./contracts/cloud-credentials-api.md) | Updated 2026-06-16 |
| Cloud credentials UI | [contracts/cloud-credentials-ui.md](./contracts/cloud-credentials-ui.md) | Updated 2026-06-16 |
| Platform-supplied model env | [contracts/platform-supplied-llm.md](./contracts/platform-supplied-llm.md) | Complete (2026-06-20) |
| Quickstart | [quickstart.md](./quickstart.md) | Complete |

## Implementation Notes (for `/speckit-tasks`)

1. **Phase 1 — Setup**: contracts, pyproject `--cov` entries matching existing pattern, `.env.example`.
2. **Phase 2 — Foundational**: store + FR-010 in `apply_scan` from day one; model store extensions; audit helpers.
3. **Phase 3 — US1**: detector + scan API + UI for **all three hyperscalers**.
4. **Phase 4 — US2**: approve/probe/audit + **full validation/enable** + incomplete-field UI + platform model sync.
5. **Phase 5 — US3**: providers + `GET /v1/agent/models` + AgentChat reads API.
6. **Phase 6 — US4**: rescan hardening (2–5s), single-flight, re-approval messaging.
7. **Phase 7 — Polish**: coverage gate, quickstart, docs, G1–G5 regression tests.

**Dependency order**: 006 model validation + store must remain compatible; gate enable on `validation_status=passed` unchanged.
