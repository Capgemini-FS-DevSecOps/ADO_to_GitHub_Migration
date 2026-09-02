# Architecture — ADO2GitHub Migration Platform

Technical architecture for the **ado2gh** migration accelerator: CLI, REST API, web console, and PEV agent.

**Version:** 5.2 · **Last updated:** 2026-08

---

## System overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Migration UI (Next.js 14)                               │
│  Dashboard · Discovery · Migrate · Monitor · Agent · Settings               │
│  OrchestrateAI-themed console · profile-scoped state · RBAC                 │
└───────────────────────────────┬─────────────────────────────┬───────────────┘
                                │ REST (cookie session)      │
              ┌─────────────────▼──────────────┐   ┌─────────▼──────────────┐
              │  Accelerator API (:8080)      │   │  Agent service (:8090)  │
              │  services/accelerator_api       │   │  services/agent         │
              │  FastAPI · auth · profiles     │   │  PEV sessions ·         │
              │  pipeline runs · settings     │   │  LangGraph orchestrator │
              └─────────────────┬──────────────┘   └─────────┬──────────────┘
                                │                            │
                                └────────────┬───────────────┘
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ado2gh Python package                               │
│  CLI (ado2gh/cli/) · core migration · phases · pipelines · reporting        │
│  api/ (accelerator SDK, pipeline runner, settings, auth, agentic routes)    │
│  agents/ (LangGraph migration agent: graph, nodes, guardrails, HITL)        │
│  state/ (SQLite · PostgreSQL · DynamoDB via factory)                        │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
  Azure DevOps REST       GitHub REST / git        State store
  (projects, pipelines,   (mirror / GEI,          (migrations, risk scores,
   work items, wiki)        Actions workflows)      pipeline inventory, gates)
```

### Deployment modes

| Mode | Compose file | State backend | Typical use |
|------|--------------|---------------|-------------|
| Local dev | `docker-compose.yml` | SQLite (`data/`) | Laptop, IDE agent |
| Production | `docker-compose.prod.yml` | PostgreSQL | Team console |

Environment: `ADO2GH_STORAGE_BACKEND=sqlite|postgres|dynamodb` (DynamoDB for serverless deploys; no dedicated compose file)

---

## Layer responsibilities

### CLI (`ado2gh/cli/`)

Click entry point (`ado2gh/cli/main.py`). Commands mirror operational workflows:

- **Discovery & planning:** `discover`, `plan`, `phase assign`, `phase plan`
- **Execution:** `run`, `phase run`, `pipelines inventory`
- **Validation & ops:** `validate`, `report`, `rollback`, `ado-cleanup`, `token-status`

Lazy imports inside command handlers keep startup fast.

### Accelerator API (`ado2gh/api/` + `services/accelerator_api/`)

HTTP façade used by the web UI and agent service:

| Area | Key modules |
|------|-------------|
| Migration runs | `pipeline_runner.py`, `accelerator.py`, `migration_work_plan.py` |
| Profiles & discovery | `settings_store.py`, `profile_discovery.py`, `migration_scan.py` |
| Auth & RBAC | `auth/`, `platform_rbac.py`, `auth_routes.py` |
| Audit history | `agentic_routes.py` (`/v1/history/*` search, event types, CSV export) |
| LLM settings | `llm/llm_model_store.py`, `llm/model_catalog.py`, `llm/model_validation.py`, `connectivity_store.py` |
| Validation | `validation_run.py` (profile-first, upload-based) |

**Pipeline steps (UI migrate flow):** connect → inventory → readiness → analyze_deps → migrate_repos → convert_pipelines → validate

**Pipeline steps (full accelerator flow):** connect → discover → inventory → readiness → assign → analyze_deps → migrate_repos → convert_pipelines → convert_metadata → validate

Each scoped migration step (`migrate_repos`, `convert_pipelines`, `convert_metadata`) runs only its scope handlers; secret mapping is part of `analyze_deps` (spec 009). `migration_work_plan.py` builds per-repo work items with categories (`migrate_repo`, `convert_metadata`, `manual_setup`) and blocker hints (missing inventory, service connections, variable groups).

Dry-run pipelines finish as `dry_run_complete` and do not write migration completion records.

### Agent service (`services/agent/`)

Separate FastAPI process hosting the LangGraph PEV agent (spec 012):

- **Graph** (`ado2gh/agents/migration_agent/graph/`) — four-agent LangGraph (Orchestrator → Planner → Executor → Validator) with conditional-edge PEV loop; max 3 retries per cycle
- **Runtime** (`ado2gh/agents/migration_agent/runtime/`) — provider-agnostic LangChain LLM bridge, SSE streaming of agent thinking, context window management
- **Scope guardrails** (`ado2gh/agents/migration_agent/policies.py`) — migration-only replies; refuses off-topic and prohibited requests
- **Tool guardrails** (`ado2gh/agents/migration_agent/guardrails.py`) — plan authorization, deletion confirmation, ADO read-only enforcement
- **Internal tools:** `fetch_profile_discovery`, `build_migration_plan`, `run_migration_pev`, plus per-role tools in `migration_agent/tools/`
- **HITL** (`ado2gh/agents/migration_agent/hitl/`) — intake, dynamic forms, blockers, operator input via graph interrupts
- **Sessions** (`ado2gh/agents/migration_agent/session/`) — lifecycle, state machine, persistent store with checkpoint resume
- **Prompts** (`ado2gh/agents/migration_agent/prompts/*.md`) — orchestrator, planner, executor, validator system prompts

### Migration UI (`apps/migration-ui/`)

Next.js App Router console:

- Profile-scoped discovery, phase configuration, migrate/monitor/validation
- **Agent tab** — chat-driven PEV with task timeline (per-repo work items + blockers), thinking blocks, forms for missing info
- **Settings** — profiles, LLM models (catalog + validate-before-enable), connectivity, users
- Responsive layout + iframe embed mode (`EmbedLayout.tsx`)

---

## Core migration engine

`ado2gh/core/migration_engine.py` executes per-repo scopes:

| Scope | Handler | Notes |
|-------|---------|-------|
| `repo` | `git_scope` | `git clone --mirror` + `git push --mirror` (or GEI) |
| `pipelines` | `pipelines_scope` | ADO definition → GHA YAML |
| `work_items` | `work_items_scope` | Issues export |
| `wiki` | `wiki_scope` | Wiki content |
| `branch_policies` | `branch_policies_scope` | Policy metadata |
| `secrets` | `secrets_scope` | Names only; manifest for ops |

**Strategies:** `gei` (default, `gh gei migrate-repo`) or `mirror` (`git clone --mirror` + `git push --mirror`).

Post-migration validation compares **HEAD commit SHA** between ADO and GitHub (`reporting/post_migration_validator.py`).

---

## Phase orchestration

```
migration.yaml  →  phase assign  →  migration_phase.yaml
                         │
                         ▼
              repo_risk_scores (StateDB)
                         │
                         ▼
              phase run (BatchExecutor)
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   MigrationEngine   checkpoints    gate-check
                         │
                         ▼
              phase_gates (pass / fail / override)
```

- **RiskScorer** — 9-signal score (0–100)
- **WaveAssigner** — poc → pilot → wave1–3
- **PhaseGateChecker** — success thresholds; `--override --reason` for escalation
- **BatchExecutor** — sub-batches with SQLite/Postgres checkpoints

Profile discovery syncs scan results into `repo_risk_scores` via `profile_discovery.py`.

---

## Pipeline transformation

```
ADO pipeline definition
        │
        ▼
PipelineMetadataExtractor  (YAML / classic / release)
     │
     ▼
PipelineMetadata (normalized)
        │
        ▼
PipelineTransformer  (200+ task mappings → GHA YAML)
        │
        ▼
.github/workflows/*.yml + migration notes
```

See [PIPELINE_TRANSFORMATION_GUIDE.md](PIPELINE_TRANSFORMATION_GUIDE.md).

---

## State persistence

Factory: `ado2gh/state/factory.py` → `create_state_db()`

| Backend | Module | When |
|---------|--------|------|
| SQLite | `state/sqlite_db.py` | Local / lightweight |
| PostgreSQL | `state/postgres_db.py` | Production compose |

`create_state_db()` supports SQLite and PostgreSQL only. DynamoDB exists for the
**job store** (`DynamoDBJobStore` in `state/job_store.py`, selected by
`ADO2GH_STORAGE_BACKEND=dynamodb`), not for the migration state DB.

**Core tables:** `migrations`, `wave_runs`, `pipeline_inventory`, `pipeline_migrations`, `repo_risk_scores`, `phase_gates`, `batch_checkpoints`

**Platform tables (Postgres):** `audit_events`, `profile_scans`, `profile_scan_repos`, auth users/sessions

Phase lookups accept `PhaseType` enum **or** plain string phase ids (e.g. `"poc"`).

---

## Authentication & RBAC

- Cookie sessions via `ado2gh/auth/service.py`
- **Bootstrap** on first boot — admin account creation (`/login?bootstrap=1`)
- **Platform roles:** admin, approver, operator
- **Capabilities:** `can_operate`, `can_manage_models`, `can_approve_live_execution`
- **Live execution** — platform approval queue before non-dry-run agent/pipeline runs

**Future (not in v1):** Enterprise SSO via OIDC (`ADO2GH_SSO_ISSUER`, `ADO2GH_SSO_AUDIENCE`, `ADO2GH_SSO_JWKS_URL`) — JWT middleware on accelerator, SSO login in UI, actor from JWT claims in audit.

---

## Token management

`ado2gh/clients/token_manager.py` — round-robin PAT pool, rate-limit tracking from response headers, optional GitHub App JWT.

Multi-token env: `GH_TOKEN_1`, `GH_TOKEN_2`, …

---

## Design principles

1. **ADO-specific** — pipeline transformation, risk signals, and cleanup target ADO concepts.
2. **Execute, don't just plan** — real git mirror/GEI migrations, not metadata-only runs.
3. **Content-level validation** — commit SHA proof, not branch counts alone.
4. **Idempotent & resumable** — checkpoints, skip completed scopes, WAL SQLite.
5. **Guarded agent automation** — PEV tools require profile discovery data; no NL-only repo invention.

---

## Related documentation

| Document | Purpose |
|----------|---------|
| [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md) | **SQLite local dev** — Docker, native, CLI |
| [SETUP_GUIDE.md](SETUP_GUIDE.md) | Install, tokens, connectivity |
| [EXECUTION_MANUAL.md](EXECUTION_MANUAL.md) | End-to-end operational guide |
| [MIGRATION_RUNBOOK.md](MIGRATION_RUNBOOK.md) | Phased rollout runbook |
| [COMMAND_REFERENCE.md](COMMAND_REFERENCE.md) | CLI reference |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common failures |
| [../specs/](../specs/) | Feature specs (001–012) |
| [../CLAUDE.md](../CLAUDE.md) | AI assistant quick reference |

---

## Local development

**Default local stack uses SQLite** — no Postgres required.

```bash
cp .env.example .env
docker compose up --build
```

Native (Windows): `.\scripts\dev\run-local-agent.ps1` + `.\scripts\dev\run-ui.ps1` · CLI: `pip install -e ".[api,agent,dev]"`

Full guide: [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md)

Production (Postgres + auth):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
pytest tests/
```

Ports: UI **3000**, Accelerator **8080**, Agent **8090**.
