# Contract: Platform-Supplied LLM Configuration

**Feature**: `007-cloud-llm-credentials`  
**Scope**: Deploy-time model SKU configuration (platform team); runtime availability from **internal approval state**.

> **Filename note**: Persisted field and entity use `platform_supplied`. Implementation module may remain `platform_managed_model.py` (sync only).

## Important distinction (A3)

| Concern | Mechanism |
|---------|-----------|
| **Which model SKU** to use | Deploy-time environment variables (platform team) |
| **Whether agent may use it** | Internal state: cloud source `approved` + model `enabled` after validate |
| **Agent model picker** | `GET /v1/agent/models` per [agent-models-api.md](./agent-models-api.md) |

There is **no** deployment mode env flag. Do not gate UI or agent behavior on an env var.

---

## AWS Bedrock (primary EC2 path)

| Variable | Required | Example |
|----------|----------|---------|
| `ADO2GH_BEDROCK_MODEL_ID` | Yes (when using platform-supplied Bedrock) | `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| `AWS_REGION` | Yes | `us-east-1` |

**Credential paths** (at least one; not set by app — host/compose):

- EC2 instance profile / task role (preferred)
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
- Web identity / IRSA env vars

---

## Microsoft Foundry (v1)

| Variable | Required when platform-supplied |
|----------|--------------------------------|
| `ADO2GH_FOUNDRY_MODEL_ID` | Yes |
| `ADO2GH_FOUNDRY_ENDPOINT` | Yes |
| `AZURE_CLIENT_ID` | Yes (or managed identity) |
| `AZURE_TENANT_ID` | Yes (service principal path) |
| `AZURE_CLIENT_SECRET` | Service principal path (host-provided, not stored by app) |

Official product name: **Microsoft Foundry** (resource type may remain `Microsoft.CognitiveServices/accounts`).

---

## Google Cloud Vertex AI (v1)

| Variable | Required when platform-supplied |
|----------|-----------------------------------|
| `ADO2GH_VERTEX_MODEL_ID` | Yes |
| `GOOGLE_CLOUD_PROJECT` | Yes |
| `GOOGLE_CLOUD_REGION` | Recommended |
| `GOOGLE_APPLICATION_CREDENTIALS` | ADC file path OR GCE metadata |

---

## Docker Compose example (snippet)

```yaml
services:
  accelerator:
    environment:
      ADO2GH_BEDROCK_MODEL_ID: "anthropic.claude-3-5-sonnet-20241022-v2:0"
      AWS_REGION: "us-east-1"
  agent:
    environment:
      ADO2GH_BEDROCK_MODEL_ID: "anthropic.claude-3-5-sonnet-20241022-v2:0"
      AWS_REGION: "us-east-1"
```

---

## Startup behavior

1. Read env → `PlatformSuppliedModelConfig` per provider with model env vars set.
2. Upsert `LLMModelConfig` (`platform_supplied: true`, `credential_mode: ambient`, `enabled: false`).
3. Do **not** auto-enable; admin must approve cloud source + Validate + Enable (US2).
4. Set agent list inclusion only when source `approved` and model `enabled` (`GET /v1/agent/models`).

---

## `.env.example` additions (implementation)

Document model ID / region / endpoint variables with comments; values empty for local dev (BYOK/stub path unchanged).
