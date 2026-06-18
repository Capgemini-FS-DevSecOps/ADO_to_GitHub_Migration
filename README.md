# ado2gh — Azure DevOps to GitHub Migration Accelerator

Enterprise-grade migration platform for **Azure DevOps → GitHub** at scale: risk-based phasing, real git mirroring, pipeline transformation, web console, and Planner–Executor–Validator (PEV) agent orchestration.

**Version 5.1** · Python 3.9+ · CLI + REST API + Next.js UI

---

## Quick start

```bash
pip install -e ".[api,dev]"
export ADO_PAT=... ADO_ORG_URL=https://dev.azure.com/YOUR_ORG GH_TOKEN=...
ado2gh discover --config migration.yaml
ado2gh phase assign --config migration.yaml --output migration_phase.yaml
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
```

**Docker (recommended for UI + agent):**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
# UI http://localhost:3000 · API :8080 · Agent :8090
```

First visit: `/login?bootstrap=1` to create the platform admin.

---

## Documentation

| Guide | Description |
|-------|-------------|
| [**Architecture**](docs/ARCHITECTURE.md) | System design, services, state, agent PEV, deployment |
| [**Setup**](docs/SETUP_GUIDE.md) | Prerequisites, tokens, connectivity |
| [**Execution manual**](docs/EXECUTION_MANUAL.md) | Full operational walkthrough |
| [**Migration runbook**](docs/MIGRATION_RUNBOOK.md) | Day-by-day phased rollout |
| [**Command reference**](docs/COMMAND_REFERENCE.md) | CLI commands and flags |
| [**Pipeline transformation**](docs/PIPELINE_TRANSFORMATION_GUIDE.md) | ADO → GitHub Actions mapping |
| [**Troubleshooting**](docs/TROUBLESHOOTING.md) | Auth, git, gates, retries |
| [**Contributing**](CONTRIBUTING.md) | Dev setup and conventions |
| [**Changelog**](CHANGELOG.md) | Release history |

Feature specifications: [`specs/`](specs/) (agentic platform, auth, profiles, LLM catalog).

---

## What’s in the repo

```
ado2gh/                 Python package (CLI, engine, API SDK, agents, state)
apps/migration-ui/      Next.js migration console (OrchestrateAI theme)
services/
  accelerator_api/      FastAPI wrapper for UI
  agent/                PEV agent + MCP server
deploy/kubernetes/      K8s manifests
docs/                   Operational documentation
scripts/                Shell helpers for CLI migration and local dev
tests/                  pytest + contract tests
```

---

## Key capabilities

- **Phased migration** — poc → pilot → wave1–3 with gate checks and overrides
- **Git strategies** — `mirror` (default) or `gei` (GitHub Enterprise Importer)
- **Pipeline conversion** — 200+ ADO task mappings to GitHub Actions
- **Profile-based ops** — deployment profiles, discovery scan, phase assignment in UI
- **PEV agent** — tool-driven planner/executor/validator; per-repo work items show repo migration vs metadata conversion vs blocked secrets
- **Validation** — commit SHA verification between ADO and GitHub
- **RBAC** — admin / operator / approver; live-run approval queue
- **LLM onboarding** — catalog picker, validate-before-enable, Ollama + cloud providers

---

## Environment variables

```bash
ADO_PAT=...                    # Azure DevOps PAT
ADO_ORG_URL=https://dev.azure.com/ORG
GH_TOKEN=...                   # or GH_TOKEN_1, GH_TOKEN_2 for load balancing
ADO2GH_STORAGE_BACKEND=postgres  # sqlite | postgres | dynamodb
```

See [SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for GitHub App auth, proxy/CA, and Docker secrets.

---

## License

See repository license file. Internal enterprise use — adjust per your organization.
