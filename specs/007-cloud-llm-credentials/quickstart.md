# Quickstart: Cloud LLM Credentials (EC2 / Docker)

**Feature**: `007-cloud-llm-credentials` | **Date**: 2026-06-18 | **Updated**: 2026-06-19

Validate detect → approve → validate → agent flow for **Amazon Bedrock**, **Microsoft Foundry**, and **Google Cloud Vertex AI** on Docker. See [contracts/platform-supplied-llm.md](./contracts/platform-supplied-llm.md), [contracts/cloud-credentials-api.md](./contracts/cloud-credentials-api.md), and [contracts/agent-models-api.md](./contracts/agent-models-api.md).

## Prerequisites

- Docker Compose stack running (accelerator `:8080`, agent `:8090`, UI `:3000`)
- Host credentials available to containers (instance role, env, or ADC)
- IAM/API permissions for chosen hyperscaler model
- Admin user with `can_manage_models`
- Platform **model** env vars on accelerator + agent (example AWS):

```bash
ADO2GH_BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
AWS_REGION=us-east-1
```

No `ADO2GH_PLATFORM_MANAGED_LLM` flag — availability comes from admin approval after scan.

## 1. Presence scan (all hyperscalers)

1. Log in as admin → **Settings → Cloud credentials**.
2. Page triggers scan on first load (or click **Rescan**).
3. **Expect** within **2–5 seconds**: cards for detected providers (`aws`, `foundry`, `gcp`) with `Pending` or `Incomplete`, method labels, no secret values.
4. **Expect**: Platform-supplied model card (if env vars set) shows read-only model ID — not yet available to agent until approve + validate.

**API check**:

```bash
curl -b cookies.txt "http://localhost:8080/v1/settings/cloud-credentials?scan=true"
```

## 2. Complete incomplete config (if needed)

1. If a card shows **Incomplete**, fill missing non-secret fields (region, endpoint, project) in UI.
2. **Save** → `PATCH /v1/settings/cloud-credentials/{provider}`.
3. **Expect**: `completeness` → `complete`; Approve enabled.

## 3. Approve credentials

1. Click **Approve** on a complete provider card.
2. **Expect**: Live probe runs; on success → **Approved** + audit `cloud_credentials.approved`.
3. **Expect**: Approve on incomplete → `400 incomplete_configuration` with `missing_fields`.

## 4. Validate and enable platform model (US2)

1. Open **Settings → Models** (or Validate CTA on cloud credentials page).
2. **Expect**: Platform-supplied model read-only.
3. **Validate** → **Passed** → **Enable**.
4. **Expect**: `platform_model.available` true only after approve + enabled.

## 5. Agent smoke (US3)

1. Open **Agent** tab as operator.
2. `GET http://localhost:8090/v1/agent/models` — **Expect**: approved+enabled cloud model in `models[]`.
3. Select model in picker → send test message.
4. **Expect**: LLM response via ambient credentials; no API key in network tab.

## 6. Rescan / re-approval

1. Change `AWS_REGION` (or Foundry endpoint) in compose → restart.
2. **Rescan** — **Expect** within 2–5s: source → **Pending** (FR-010).
3. Re-approve → re-validate before agent uses ambient model again.

## 7. BYOK fallback (SC-004)

1. Deploy without cloud credential signals.
2. **Expect**: Empty/absent sources; BYOK on Models still works in same session.
3. No errors from cloud credentials feature.

## Local dev (no cloud)

- Omit hyperscaler model env vars; use stub/Ollama/BYOK from feature `006`.
- Cloud credentials UI shows empty state.

## Success criteria mapping

| ID | Check |
|----|-------|
| SC-001 | Steps 1–4 complete in < 10 min on real EC2 |
| SC-002 | Approve/reject/revoke/rescan in audit |
| SC-003 | No secrets in API JSON, audit, or UI (see contract redaction rules) |
| SC-004 | Step 7 BYOK still works |
| SC-005 | Step 6 re-approval within 2–5s |
