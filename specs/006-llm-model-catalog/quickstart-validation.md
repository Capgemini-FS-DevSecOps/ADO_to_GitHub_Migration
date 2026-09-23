# Quickstart validation — Feature 006

**Date**: 2026-06-16  
**Environment**: Automated pytest + local dev stack  
**Feature**: `006-llm-model-catalog`

## Automated sign-off

| Scenario | Method | Status |
|----------|--------|--------|
| 1 — Cloud catalog + validate + enable | `tests/test_006_quickstart_scenarios.py::test_scenario1_*` | PASS (automated) |
| 2 — Preset fallback / stale banner | `tests/test_006_quickstart_scenarios.py::test_scenario2_*` | PASS (automated) |
| 5 — Enable blocked without validation | `tests/test_006_quickstart_scenarios.py::test_scenario5_*` | PASS (automated) |
| 7 — Operator denied models/connectivity | `tests/test_006_quickstart_scenarios.py::test_scenario7_*` | PASS (automated) |
| 8 — Audit redaction | `tests/test_006_quickstart_scenarios.py::test_scenario8_*` | PASS (automated) |

Full suite: `pytest tests/test_006_quickstart_scenarios.py tests/test_model_validation.py tests/test_connectivity_store.py -q`

## Manual scenarios (proxy / Ollama lab)

| Scenario | Status | Notes |
|----------|--------|-------|
| 3 — Corporate proxy + custom CA | **Deferred** | Requires lab proxy/CA; API `PUT /connectivity` + `POST /connectivity/test` implemented |
| 4 — Local Ollama model | **Deferred** | Requires running Ollama; discovery + `OllamaProvider` implemented |
| 6 — Custom model ID override | **Partial** | Toggle + override gate covered by automated store/API tests |

### Scenario 3 manual steps (when lab available)

1. Settings → Connectivity → enable proxy, paste CA PEM → Save.
2. Confirm notice: “Re-validate models before enabling”.
3. Test connection → expect pass through proxy or categorized proxy/tls failure.

### Scenario 4 manual steps (when Ollama available)

1. `ollama pull qwen2.5:7b` (or equivalent).
2. Settings → Models → Ollama → base URL `http://localhost:11434`.
3. Select model → Validate → Save & enable → Agent dry-run.

## Coverage

- pytest (006 modules): ≥85% on gated `ado2gh` modules (see CI `test` job).
- UI: `cd apps/migration-ui && npm test` (permissions + llmSettings helpers).
