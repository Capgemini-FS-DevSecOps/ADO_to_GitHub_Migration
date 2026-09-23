# Research: LLM Model Catalog, Validation, and Connectivity Settings

**Feature**: `006-llm-model-catalog` | **Date**: 2026-06-16

## R1 — Catalog source strategy (live vs preset)

**Decision**: **Live primary, preset fallback** (spec clarification Q4). For `openai`, call `GET {base}/v1/models` with admin-supplied API key when credentials present. For `anthropic`, use bundled preset list as primary catalog (Anthropic has no public models list API equivalent to OpenAI); optionally refresh presets in repo releases. On live failure or missing credentials, return preset entries with `stale: true`.

**Rationale**: Matches FR-002 and edge case for offline/rate-limited providers. Presets eliminate typos for common models (SC-005).

**Alternatives considered**:
- *Preset-only* — rejected; stale model names when providers ship new SKUs.
- *Live-only* — rejected; breaks air-gapped catalog browse before key entry.

---

## R2 — Environment connectivity profile storage

**Decision**: New `ConnectivityStore` persisting `{ADO2GH_DATA_DIR}/connectivity_profile.json` with structure mirroring `LLMModelStore` (public fields + `_secrets` sidecar for `proxy_password` and `custom_ca_pem`). Single profile per deployment environment (spec Q1).

**Rationale**: Proxy and CA are shared across all cloud outbound calls; avoids duplicating on each model row. Invalidating all model validations when connectivity changes satisfies deferred plan note.

**Alternatives considered**:
- *Environment variables only* — rejected; SC-006 requires self-service Settings UI.
- *Per-model proxy* — rejected in clarification session.

---

## R3 — Corporate proxy and custom CA with httpx

**Decision**: Central `build_llm_http_client(*, for_cloud: bool)` returning `httpx.Client` with:
- `proxy=` from connectivity profile when `proxy_enabled` and `for_cloud=True`
- `verify=` as SSL context: system defaults + loaded custom CA PEM when configured
- Default timeout 30s for validation/catalog (SC-002)

Local/Ollama URLs (`for_cloud=False`) use direct client without proxy.

**Rationale**: httpx natively supports `proxy` and custom `verify` SSLContext; keeps logic in one module testable with mocks.

**Alternatives considered**:
- *Global env `HTTP_PROXY`* — rejected; not self-service and conflates host + UI config.
- *Custom CA via OS trust store only* — rejected; spec Q5 requires v1 upload/paste in Settings.

---

## R4 — Validation semantics

**Decision**: Validation performs minimal inference check:
- **OpenAI/Anthropic**: single short chat completion (`"ping"` / max_tokens 5)
- **Ollama**: `POST /api/chat` or `/v1/chat/completions` with selected model name
- Returns categorized failure: `credentials`, `network`, `proxy`, `model_not_found`, `timeout`, `tls`
- **Required before enable/default** (spec Q3): API rejects `enabled`/`default_for_agent` unless last validation `passed`
- Draft validate accepts unsaved body; saved validate uses store secrets

**Rationale**: FR-004/FR-005/FR-011; avoids full agent PEV session for connectivity proof.

**Alternatives considered**:
- *HEAD/health only* — rejected; some gateways return 200 without model access.
- *Optional validation with warning* — rejected in clarification.

---

## R5 — Local / Ollama provider

**Decision**: Add provider enum value `ollama` (alias UX label “Local / self-hosted”). Model record stores `base_url` (required). Discovery via `GET {base_url}/api/tags` parsing `models[].name`. Runtime: `OllamaProvider` using native chat API; optional `api_key` if gateway requires bearer token.

**Rationale**: Ollama is de facto standard for local Qwen/Llama stacks; `/api/tags` is stable discovery endpoint.

**Alternatives considered**:
- *Generic OpenAI-compatible only* — partial; many local stacks are Ollama-native first.
- *Embed Ollama in Compose* — out of scope per spec.

---

## R6 — Manual model ID override

**Decision**: Environment flag `allow_custom_model_id` (default **false**). UI shows free-text `model_id` only when toggle enabled **and** (catalog fetch failed or admin explicitly expands override). API accepts `catalog_source: override` with typed `model_id` when toggle on.

**Rationale**: Spec clarification Q2; preserves catalog-first UX while allowing regulated exceptions.

**Alternatives considered**:
- *Always show override* — rejected; reintroduces typo risk.
- *Never allow* — rejected; exotic local gateways need escape hatch.

---

## R7 — UI information architecture

**Decision**: New Settings tab **Connectivity** (`/settings/connectivity`, admin-only) for proxy, custom CA, and override toggle. **Models** tab retains model CRUD + catalog picker + Validate. Operators unchanged (read-only models list denial).

**Rationale**: Separates infrequent env network config from per-model onboarding; matches FR-007 environment-level wording.

**Alternatives considered**:
- *All on Models page* — rejected; cluttered; proxy is not per-model.

---

## R8 — Preset catalog maintenance

**Decision**: Ship static `ado2gh/api/data/llm_presets.json` versioned with repo. Include top OpenAI chat models + Anthropic Claude 3.x/4.x IDs with human descriptions. Update via normal release cadence (not runtime auto-sync).

**Rationale**: Predictable offline behavior; no external dependency for preset content.

**Alternatives considered**:
- *Download presets from CDN* — rejected; corporate egress may block.
