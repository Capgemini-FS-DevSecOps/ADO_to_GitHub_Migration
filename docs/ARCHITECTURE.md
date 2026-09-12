# Architecture — ADO2GitHub Migration Platform

Technical architecture for the **ado2gh** migration accelerator: CLI, REST API, web console, and PEV agent.

**Version:** 5.3 · **Last updated:** 2026-09

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
│  api/ (accelerator SDK, pipeline runner, settings, LLM, live approvals)     │
│  agents/ (LangGraph migration agent: graph, nodes, guardrails, HITL)        │
│  audit/ (redaction choke point + audit writer) · auth/ (users, RBAC)        │
│  state/ (SQLite · PostgreSQL via factory; DynamoDB job store)               │
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

The production compose file ships no default credentials. `POSTGRES_PASSWORD` and the
session secret are required interpolations — the stack refuses to start without them rather
than falling back to a shared value — and the database port is not published to the host.
The scheduled migration workflow (`.github/workflows/migrate-repo.yml`) runs its transfer
job in a GitHub environment, so a reviewer approves before anything non-dry-run executes.

---

## Layer responsibilities

### CLI (`ado2gh/cli/`)

Click entry point (`ado2gh/cli/main.py`). Commands mirror operational workflows:

- **Discovery & planning:** `discover`, `plan`, `phase assign`, `phase plan` — `ado2gh/cli/discover.py`, `ado2gh/cli/phase.py`
- **Execution:** `run`, `phase run`, `pipelines inventory` — `ado2gh/cli/migration.py`, `ado2gh/cli/pipelines.py`
- **Validation & ops:** `validate`, `report`, `rollback`, `ado-cleanup`, `token-status` — `ado2gh/cli/misc.py`

Lazy imports inside command handlers keep startup fast. Repo-list arguments are parsed by
`ado2gh/api/repo_input.py`, shared with the API so a file of repo names means the same
thing on both paths.

### Accelerator API (`ado2gh/api/` + `services/accelerator_api/`)

HTTP façade used by the web UI and agent service. The `ado2gh/api/` package holds the logic;
`services/accelerator_api/` holds the HTTP routes. Route modules never live inside the
`ado2gh` package.

| Area | Logic (`ado2gh/api/`) | Routes (`services/accelerator_api/`) |
|------|-----------------------|--------------------------------------|
| Migration runs | `ado2gh/api/pipeline_runner.py`, `ado2gh/api/accelerator.py`, `ado2gh/api/migration_work_plan.py` | `services/accelerator_api/routes/migrate_routes.py` with `services/accelerator_api/routes/migrate_guard.py`, `services/accelerator_api/routes/migrate_scope.py`, `services/accelerator_api/routes/migrate_routes_models.py` |
| Profiles & discovery | `ado2gh/api/settings_store.py`, `ado2gh/api/profile_discovery.py`, `ado2gh/api/migration_scan.py` | `services/accelerator_api/routes/profile_routes.py`, `services/accelerator_api/routes/profile_credential_routes.py` |
| Auth & RBAC | `ado2gh/auth/`, `ado2gh/api/platform_rbac.py` | `services/accelerator_api/auth_routes.py` |
| Live approvals | `ado2gh/api/live_approval_store.py` | `services/accelerator_api/routes/approval_routes.py` |
| Audit history | `ado2gh/audit/writer.py` | `services/accelerator_api/routes/history_routes.py` (`/v1/history/*` search, event types, CSV export) |
| LLM settings | `ado2gh/api/llm/llm_model_store.py`, `ado2gh/api/llm/model_catalog.py`, `ado2gh/api/llm/model_validation.py`, `ado2gh/api/connectivity_store.py` | `services/accelerator_api/routes/settings_routes.py` |
| Validation | `ado2gh/api/validation_run.py` (profile-first, upload-based) | `services/accelerator_api/routes/pipeline_routes.py` |

**Pipeline steps (UI migrate flow):** connect → inventory → readiness → analyze_deps → migrate_repos → convert_pipelines → validate

**Pipeline steps (full accelerator flow):** connect → discover → inventory → readiness → assign → analyze_deps → migrate_repos → convert_pipelines → convert_metadata → validate

Each scoped migration step (`migrate_repos`, `convert_pipelines`, `convert_metadata`) runs only its scope handlers; secret mapping is part of `analyze_deps` (spec 009). `ado2gh/api/migration_work_plan.py` builds per-repo work items with categories (`migrate_repo`, `convert_metadata`, `manual_setup`) and blocker hints (missing inventory, service connections, variable groups). Work items and blockers use one canonical wire shape — `scope` and `blocker` keys — normalised by `sync_work_item_wire_keys` so the API, the agent and the console read the same field names.

Dry-run pipelines finish as `dry_run_complete` and do not write migration completion records.

**Live execution gate.** Every route on the migrate router depends on
`guard_live_migration` in `services/accelerator_api/routes/migrate_guard.py` — one choke
point for the whole router rather than a check per handler. Dry runs pass straight through,
so local development stays permissive for everything reversible. A live request is decided
by `operator_requires_live_approval` in `ado2gh/api/platform_rbac.py`: it proceeds only when
the caller holds the `can_approve_live_execution` capability, any other operator is parked
in the approval queue, and a live request carrying no identity is refused with 401. Turning
authentication off does not lift the gate.

`ado2gh/api/live_approval_store.py` is the only source of live authority for a pipeline run.
A client cannot certify itself: the request models reject unknown fields, so a body that
tries to assert its own approval is a 422.

### Agent service (`services/agent/`)

Separate FastAPI process hosting the LangGraph PEV agent (spec 012):

- **Graph** (`ado2gh/agents/migration_agent/graph/`) — four-agent LangGraph (Orchestrator → Planner → Executor → Validator) with conditional-edge PEV loop; max 3 retries per cycle
- **Runtime** (`ado2gh/agents/migration_agent/runtime/`) — provider-agnostic LangChain LLM bridge, SSE streaming of agent thinking, context window management
- **Routes** (`services/agent/routes/`) — one module per concern (session, message, form, plan, execution, model, run) over shared helpers in `services/agent/routes/_helpers.py`
- **Scope guardrails** (`ado2gh/agents/migration_agent/policies.py`) — migration-only replies; refuses off-topic and prohibited requests
- **Tool guardrails** (`ado2gh/agents/migration_agent/guardrails.py`) — plan authorization, deletion confirmation, ADO read-only enforcement
- **Live mode** — `enforce_live_mode_request` in `ado2gh/agents/migration_agent/policies.py` is the single identity-and-capability check behind every route that flips a session to live; `resolve_execution_dry_run` is the single place session-level and plan-level modes are reconciled, and the safest of the two wins, so a plan cannot quietly widen a session that was created as a dry run
- **Internal tools:** `fetch_profile_discovery`, `build_migration_plan`, `run_migration_pev`, plus per-role tools in `migration_agent/tools/`
- **HITL** (`ado2gh/agents/migration_agent/hitl/`) — intake, dynamic forms, blockers, operator input via graph interrupts
- **Sessions** (`ado2gh/agents/migration_agent/session/`) — lifecycle, state machine, persistent store with checkpoint resume
- **Prompts** (`ado2gh/agents/migration_agent/prompts/*.md`) — orchestrator, planner, executor, validator system prompts

### Migration UI (`apps/migration-ui/`)

Next.js App Router console:

- Profile-scoped discovery, phase configuration, migrate/monitor/validation
- **Agent tab** — chat-driven PEV with task timeline (per-repo work items + blockers), thinking blocks, forms for missing info
- **Settings** — profiles, LLM models (catalog + validate-before-enable), connectivity, users
- Responsive layout + iframe embed mode (`apps/migration-ui/src/components/EmbedLayout.tsx`)

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

**Strategies:** `gei` (default, `gh ado2gh migrate-repo`) or `mirror` (`git clone --mirror` + `git push --mirror`).

**Execution mode.** Internal signatures take `ExecutionMode` (`DRY_RUN` or `LIVE`) from
`ado2gh/models.py` rather than a `dry_run` boolean, so a call site says which mode it runs
in instead of leaving a bare `True` to be read at the wrong end. The external shapes are
unchanged and boolean — the `--dry-run` CLI flag, the `dry_run` field in HTTP bodies, the
YAML config key and the `dry_run` database column — and convert once at the boundary with
`ExecutionMode.from_dry_run(dry_run=...)`. `DRY_RUN` remains the default everywhere a
default existed, so an omitted argument never runs live.

**Concurrency guard.** `ado2gh/core/conflict_detection.py` answers "is another run already
holding this repo?" and fails closed. `get_repo_conflict_reason` returns a reason string
when a check cannot complete — a transient error in the repo lock manager or the pipeline
run store counts as a conflict rather than as "no conflict, proceed" — and
`clear_stale_in_progress_migrations` refuses to clear an in-progress row on an inconclusive
check. The reason is carried through to the operator, so a held migration says why it was
held.

**Workflow push readiness.** A live push of generated workflows
(`ado2gh/pipelines/push_workflows.py`) is graded by `workflow_push_readiness` in
`ado2gh/reporting/pipeline_readiness.py`, using the same
auto/assisted/manual classification the `pipeline-readiness` command reports. A pipeline
graded `manual` blocks the push and the reason is returned to the caller; one graded
`assisted` is pushed with its caveats appended to the pull request body, so the reviewer
sees that it needs manual attention. `--dry-run` remains the ungated preview path.

Post-migration validation compares **HEAD commit SHA** between ADO and GitHub (`ado2gh/reporting/post_migration_validator.py`).

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

- **RiskScorer** (`ado2gh/phase/risk_scorer.py`) — 9-signal score (0–100)
- **WaveAssigner** (`ado2gh/phase/wave_assigner.py`) — poc → pilot → wave1–3
- **PhaseGateChecker** (`ado2gh/phase/gate_checker.py`) — success thresholds; `--override --reason` for escalation
- **BatchExecutor** (`ado2gh/phase/batch_executor.py`) — sub-batches with SQLite/Postgres checkpoints

Scoring itself lives in `ado2gh/phase/repo_scoring.py` and is shared: the `phase assign`
command and the API's profile scan run the same code, so a repo gets the same score
whichever path reached it. `phase assign` persists the scores and writes
`migration_phase.yaml` with credentials stripped.

Gates cannot be walked past silently. A run that would skip a blocking gate has to escalate
through `PhaseGateChecker.override`, which requires a reason and records it; the gate is
always evaluated first, so the audit trail shows both the failure and the decision to
proceed.

Profile discovery syncs scan results into `repo_risk_scores` via
`ado2gh/api/profile_discovery.py`.

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

Factory: `ado2gh/state/factory.py` → `create_state_db(db_path, *, backend=None)`

| Backend | Module | When |
|---------|--------|------|
| SQLite | `ado2gh/state/sqlite_db.py` | Local / lightweight |
| PostgreSQL | `ado2gh/state/postgres_db.py` | Production compose |

Backend selection is explicit and ordered: an argument passed by the caller wins, otherwise
`ADO2GH_STORAGE_BACKEND` decides. The SQLite path works the same way — the caller's
`db_path` wins, otherwise `ADO2GH_SQLITE_PATH`, otherwise the built-in default. An
unrecognised backend name is an error that names the variable that set it, rather than a
silent fall back to SQLite.

`create_state_db` supports SQLite and PostgreSQL only. DynamoDB exists for the
**job store** (`DynamoDBJobStore` in `ado2gh/state/job_store.py`, selected by
`ADO2GH_STORAGE_BACKEND=dynamodb`), not for the migration state DB. Job claims there are
conditional writes, so two workers cannot claim the same job, and a losing claim is audited.

Scan payloads are packed and unpacked by `ado2gh/state/scan_payload.py`, next to the store
that persists them rather than in the API layer that happens to call it.

**Core tables:** `migrations`, `wave_runs`, `pipeline_inventory`, `pipeline_migrations`, `repo_risk_scores`, `phase_gates`, `batch_checkpoints`

**Platform tables (Postgres):** `audit_events`, `profile_scans`, `profile_scan_repos`, auth users/sessions

Phase lookups accept `PhaseType` enum **or** plain string phase ids (e.g. `"poc"`).

---

## Authentication & RBAC

- Cookie sessions via `ado2gh/auth/service.py`. The session cookie is `HttpOnly` and
  `SameSite=Lax`, gains `Secure` when the request arrives over HTTPS, and its `Max-Age`
  matches the server-side session lifetime instead of outliving it.
- **Bootstrap** on first boot — admin account creation (`/login?bootstrap=1`)
- **Platform roles:** admin, approver, operator, coordinator
- **Capabilities:** `can_operate`, `can_manage_models`, `can_approve_live_execution`
- **Live execution** — platform approval queue before non-dry-run agent/pipeline runs

Authorisation decisions are **capability-derived, never role-name-derived**: a new role that
can operate but not approve is gated by the existing checks without any of them being
edited. `ADO2GH_AUTH_ENABLED=false` keeps local development permissive for dry runs and
audit reads, but it does not lift the live-execution checks — those require an identity and
the `can_approve_live_execution` capability in every configuration.

The GitHub proxy (`services/accelerator_api/routes/proxy_routes.py`) is not read-only:
`POST`, `PATCH`, `PUT` and `DELETE` through it require the approve-live capability and are
written to the audit log with the actor, method and endpoint. `GET` is unrestricted.

The agent's `/v1/internal/*` routes are for service-to-service calls and fail closed. They
require `ADO2GH_INTERNAL_TOKEN`, compared in constant time; if the variable is unset the
whole range answers 401 rather than running unauthenticated.

---

## Secret handling

One masking choke point: `redact_payload` in `ado2gh/audit/redaction.py`. It matches secret
**key names** (case- and affix-insensitive, so `ado_pat`, `GH_TOKEN`, `clientSecret` and
`apiKey` all hit) and secret **value shapes** (GitHub token prefixes, `Bearer <token>`, a
bare ADO PAT, `key=value` pairs), because key names alone cannot reach a secret that arrives
inside free text.

Everything that can carry a secret routes through that one function:

- `SecretRedactingFilter` in `ado2gh/logging_config.py` is attached to the root log handler,
  so records propagated from any module's logger are masked. It never raises: if redaction
  fails, the record content is dropped and logging stays alive.
- Agent messages are masked where they are constructed, not at the audit boundary, so the
  SSE stream, the chat transcript and the persisted session rows all show the masked form.
- Subprocess output capture in `ado2gh/core/scopes/git_scope.py` delegates to the same
  function rather than keeping its own pattern list.

LLM provider keys are sent in request bodies, never in a URL query string.

Service connection secrets are never migrated — only their names are readable from ADO. The
`service-connections` command emits a manifest of GitHub secret names and OIDC setup steps
for the operations team to fill in.

**Future (not in v1):** Enterprise SSO via OIDC (`ADO2GH_SSO_ISSUER`, `ADO2GH_SSO_AUDIENCE`, `ADO2GH_SSO_JWKS_URL`) — JWT middleware on accelerator, SSO login in UI, actor from JWT claims in audit.

---

## Token management

`ado2gh/clients/gh_token_manager.py` — round-robin PAT pool, rate-limit tracking from response headers, optional GitHub App JWT.

Multi-token env: `GH_TOKEN_1`, `GH_TOKEN_2`, …

---

## Design principles

1. **ADO-specific** — pipeline transformation, risk signals, and cleanup target ADO concepts.
2. **Execute, don't just plan** — real git mirror/GEI migrations, not metadata-only runs.
3. **Content-level validation** — commit SHA proof, not branch counts alone.
4. **Idempotent & resumable** — checkpoints, skip completed scopes, WAL SQLite.
5. **Guarded agent automation** — PEV tools require profile discovery data; no NL-only repo invention.
6. **One choke point per cross-cutting concern** — masking, the live-execution gate, backend
   selection and repo scoring each have a single implementation that every caller routes
   through. A guard added to a router covers the routes added to it later; a guard copied
   into nine handlers does not.
7. **Fail closed** — when a safety check cannot complete, the answer is "unsafe". An
   inconclusive conflict check blocks the migration, a missing internal token rejects the
   request, and an unrecognised backend name is an error rather than a fallback.

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
| [../specs/](../specs/) | Feature specs (001–013) |
| [STRUCTURAL_CHANGELOG.md](STRUCTURAL_CHANGELOG.md) | Append-only ledger of file moves and deletions |
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
