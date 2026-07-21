# Research: Cloud-Hosted LLM Credential Detection and Admin Approval

**Feature**: `007-cloud-llm-credentials` | **Date**: 2026-06-18 | **Updated**: 2026-06-19

## R1 — Presence detection vs live probe (clarification)

**Decision**: **Presence-only on scan**; **live probe on Approve and model Validate** (spec session 2026-06-18 Q3). Scan inspects env vars, credential file paths, and EC2/ECS metadata URL reachability without calling Bedrock/Microsoft Foundry/Vertex APIs. Approve runs provider-specific probe; failed probe keeps status `pending` with categorized message.

**Rationale**: FR-001/FR-001a; avoids cloud API calls before admin attestation; air-gapped scan still works.

---

## R2 — One source per hyperscaler (clarification)

**Decision**: At most **one `CloudCredentialSource` per provider** (`aws`, `foundry`, `gcp`). Multiple methods detected → `primary_method` + `alternate_methods[]` labels; approval binds to provider.

**Rationale**: Spec Q2; simpler EC2 onboarding UX. API provider id `foundry` maps to **Microsoft Foundry** (official name; formerly Azure AI Foundry).

---

## R3 — Re-approval triggers (clarification)

**Decision**: Rescan compares snapshot hash of `{primary_method, region, project, endpoint, completeness, admin_supplied_fields}`. Any change on approved source → status `pending`, ambient use disabled, dependent platform-supplied models invalidated to `never_validated`.

**Rationale**: FR-010 method+config rule from clarification Q1.

---

## R4 — Platform-supplied model SKUs (clarification)

**Decision**: Platform team sets **model ID / region / endpoint** via deploy-time env vars; app syncs read-only `LLMModelConfig` on startup. **No** `ADO2GH_PLATFORM_MANAGED_LLM` mode flag. Runtime availability = internal approval state + validate/enable (spec session 2026-06-16 A3).

| Variable | Required when | Purpose |
|----------|---------------|---------|
| `ADO2GH_BEDROCK_MODEL_ID` | AWS platform-supplied | Bedrock foundation model ID |
| `AWS_REGION` | AWS | Bedrock region |
| `ADO2GH_FOUNDRY_MODEL_ID` | Microsoft Foundry platform-supplied | Model/deployment id |
| `ADO2GH_FOUNDRY_ENDPOINT` | Microsoft Foundry | Project endpoint URL |
| `ADO2GH_VERTEX_MODEL_ID` | GCP platform-supplied | Vertex model resource id |
| `GOOGLE_CLOUD_PROJECT` | GCP | GCP project |
| `GOOGLE_CLOUD_REGION` | GCP | Recommended region |

Credential env vars (host/compose — not stored by app): standard AWS/Azure/GCP patterns per contracts.

**Rationale**: Spec Q4/Q5 + remediation; Docker Compose / EC2 user-data injection; no secrets in UI.

---

## R5 — Persistence for credential sources

**Decision**: `CloudCredentialsStore` at `{ADO2GH_DATA_DIR}/cloud_credentials.json` — public fields per source + approval metadata + `admin_supplied_fields`; **no secret values persisted**.

**Rationale**: Mirrors `ConnectivityStore` / `LLMModelStore` from `006`; file store sufficient for single-tenant EC2.

---

## R6 — AWS presence detection signals

**Decision**: AWS source **complete** when `AWS_REGION` set AND at least one credential path:
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` present (values not read/logged), OR
- `AWS_WEB_IDENTITY_TOKEN_FILE` / `AWS_ROLE_ARN` (EKS/IRSA), OR
- ECS relative URI env present, OR
- EC2 IMDS reachable at `169.254.169.254` (HEAD/short GET with 1s timeout; presence only).

`primary_method` priority: instance_role > web_identity > env_keys.

---

## R7 — Microsoft Foundry and GCP presence + live probes (v1)

**Product naming (F1)**: **Microsoft Foundry** is the official hyperscaler AI platform name (2026). Resource type may remain `Microsoft.CognitiveServices/accounts`; SDK surface is `azure-ai-projects` 2.x + `openai` client with project `base_url`.

**Presence — Microsoft Foundry (`foundry`)**:
- **Complete** when `ADO2GH_FOUNDRY_ENDPOINT` (or `AZURE_OPENAI_ENDPOINT` legacy alias) set AND (`AZURE_CLIENT_ID` + `AZURE_TENANT_ID` + host-provided secret OR managed identity via `MSI_ENDPOINT` / `IDENTITY_ENDPOINT`).
- **Incomplete** when endpoint or identity signals missing — admin may `PATCH` non-secret `endpoint`, `region`.

**Presence — GCP (`gcp`)**:
- **Complete** when `GOOGLE_CLOUD_PROJECT` + (`GOOGLE_APPLICATION_CREDENTIALS` path exists OR GCE metadata `metadata.google.internal` reachable — presence only).

**Live probes (FR-001a, all three in v1)**:
- **AWS**: `bedrock-runtime` minimal `converse` or `invoke_model` with platform model id.
- **Microsoft Foundry**: minimal chat completion via project endpoint using ambient Entra/managed identity (prefer `openai` + Foundry project URL per Microsoft docs).
- **GCP**: Vertex `generateContent` REST or `google-cloud-aiplatform` minimal ping.

**Rationale**: v1 includes all three hyperscalers per spec remediation I1.

---

## R8 — Hyperscaler runtime providers

**Decision**: Add `bedrock`, `foundry`, `vertex` to `LLMProviderSpec`. Runtime:
- Bedrock: `boto3` `bedrock-runtime`
- Foundry: `openai` client + project endpoint + Azure credential chain
- Vertex: `google-cloud-aiplatform` or REST with ADC

Optional deps in `[project.optional-dependencies] api`.

---

## R9 — UI information architecture

**Decision**: Settings tab **Cloud credentials** at `/settings/cloud-credentials` (admin + `can_manage_models`). Models page shows read-only platform-supplied banner + Validate/Enable (US2). Agent tab reads **`GET /v1/agent/models`** for selectable cloud-backed options when approved+enabled (FR-007b, FR-016) — **does not** hide picker or gate on env flags.

**Rationale**: Spec remediation A3/I4; separates credential approval from agent consumption.

---

## R10 — Startup sync hook

**Decision**: `platform_managed_model.sync_on_startup()` in accelerator + agent startup: if model env vars present, upsert read-only `LLMModelConfig` with `platform_supplied: true`, `credential_mode: ambient`, `enabled: false` until US2 validate/enable.

**Rationale**: FR-014; approval + validation still required before agent use.

---

## R11 — Docker / EC2 deployment notes

**Decision**: Document in `quickstart.md` and `.env.example`; platform team injects model env vars on existing compose — no mode flag.

**Rationale**: Platform team owns compose; product documents contract.
