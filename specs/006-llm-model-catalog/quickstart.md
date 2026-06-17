# Quickstart: LLM Model Catalog, Validation, and Connectivity

**Feature**: `006-llm-model-catalog`  
**Spec**: [spec.md](./spec.md) | **Contracts**: [llm-catalog-api.md](./contracts/llm-catalog-api.md), [connectivity-api.md](./contracts/connectivity-api.md)

## Prerequisites

- Feature `004-agent-pev-rbac` deployed (model registry, admin RBAC, Agent tab).
- Docker Compose with auth enabled (prod overlay) or local dev stack.
- Admin credentials; optional corporate proxy/CA for scenario 3.
- Optional: local Ollama on `http://localhost:11434` with a Qwen or Llama model pulled.

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
# or scripts/run-local.ps1 for dev
```

## Scenario 1 — Cloud model via catalog + validate (P1)

**Proves**: FR-001, FR-002, FR-004, FR-011, SC-001

1. Log in as **admin**.
2. Open **Settings → Models**.
3. Choose provider **OpenAI**, enter API key.
4. Confirm model dropdown populates (live or preset with stale banner).
5. Select **GPT-4o Mini** (or equivalent) — do not type model ID.
6. Click **Validate** → expect **Passed** within 30s.
7. Enable model and optionally **Set as default** → Save.
8. Open **Agent** tab → confirm model appears in picker; start dry-run session.

**API equivalent**:

```bash
curl -b cookies.txt "http://localhost:8080/v1/settings/llm-models/catalog?provider=openai" \
  -H "Cookie: ..."
curl -X POST http://localhost:8080/v1/settings/llm-models/validate -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","model_id":"gpt-4o-mini","api_key":"sk-..."}'
```

## Scenario 2 — Preset fallback when live catalog fails (P1)

**Proves**: FR-002 fallback, edge case stale notice

1. Block outbound to OpenAI **or** use invalid network path without proxy.
2. Open Models → OpenAI → enter valid key.
3. Expect preset list with **“Catalog may be outdated”** banner.
4. Select preset model → Validate (if network allows inference) or expect categorized **network/proxy** failure.

## Scenario 3 — Corporate proxy + custom CA (P2)

**Proves**: FR-007, FR-007a, SC-006

1. **Settings → Connectivity**.
2. Enable proxy; enter host/port/credentials per your lab proxy.
3. Paste corporate inspection CA PEM → Save.
4. Click **Test connection** (if implemented) or Validate a cloud model.
5. Confirm success through proxy or **proxy/tls** category on misconfiguration.

## Scenario 4 — Local Ollama model (P2)

**Proves**: FR-009, SC-004

1. Ensure Ollama running with e.g. `ollama pull qwen2.5:7b`.
2. **Settings → Models** → provider **Ollama / local**.
3. Base URL `http://localhost:11434` → catalog lists pulled models.
4. Select model → **Validate** → **Passed**.
5. Enable → Agent dry-run session uses local model (check agent logs for ollama base URL, not stub).

## Scenario 5 — Validation gate blocks enable (P1)

**Proves**: FR-011, clarification Q3

1. Add model configuration without clicking Validate.
2. Confirm **Enable** and **Set as default** are disabled in UI.
3. API `POST` with `"enabled": true` without prior validation → expect `400`.

## Scenario 6 — Custom model ID override (P2)

**Proves**: FR-010, clarification Q2

1. **Settings → Connectivity** → enable **Allow custom model ID** → Save.
2. Models → add OpenAI model → if catalog empty, expand override field.
3. Enter known model id → Validate → Enable.

## Scenario 7 — Operator read-only (regression)

**Proves**: FR-012

1. Log in as **operator** in second browser.
2. Navigate to `/settings/models` and `/settings/connectivity`.
3. Expect access denied / read-only messaging; Agent picker still lists admin-enabled models.

## Scenario 8 — Audit redaction (CA-003 / CA-004)

**Proves**: FR-014, SC-003

1. Save connectivity with proxy password and custom CA.
2. Validate a model (pass and fail cases).
3. Inspect audit events in StateDB or audit export — no raw keys, passwords, or PEM.

```powershell
pytest tests/test_model_validation.py tests/test_connectivity_store.py -q
pytest tests/contract/test_006_llm_catalog_contracts.py -q
cd apps/migration-ui && npm test
```

## Expected automated coverage (post-implementation)

| Area | Test path |
|------|-----------|
| Catalog preset/live | `tests/test_model_catalog.py` |
| Validation categories | `tests/test_model_validation.py` |
| Connectivity secrets | `tests/test_connectivity_store.py` |
| API contracts | `tests/contract/test_006_llm_catalog_contracts.py` |
| UI permissions | `apps/migration-ui/src/lib/llmSettings.test.ts` |

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Empty catalog | Credentials for cloud; base URL for Ollama; proxy for egress |
| TLS error | Custom CA in Connectivity settings |
| Enable disabled | Run Validate first |
| Stub in agent | Model not enabled or validation not passed |
