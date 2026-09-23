# Tasks: Local Agent Development in the IDE

**Input**: Design documents from `specs/003-local-agent-ide/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED (constitution Principle VI — ≥85% line coverage on `ado2gh/agents/local/` and `services/agent/`)

**Organization**: Tasks grouped by user story for independent delivery and validation.

**Plan sync**: 2026-06-16 — dual HTTP+MCP paths, lightweight SQLite profile, stub LLM default, optional auth, unified audit store.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package scaffolding, profile config, Compose lightweight stack, env documentation

- [x] T001 Create `ado2gh/agents/local/` package with `__init__.py` per plan.md structure
- [x] T002 [P] Add `config/local-profiles.yaml` with `lightweight`, `full`, `prod-like` definitions per `contracts/local-profiles.md`
- [x] T003 [P] Create `docker-compose.lightweight.yml` (accelerator + agent only, no redis/worker) per plan.md
- [x] T004 [P] Extend `.env.example` with `LLM_PROVIDER`, `ADO2GH_LIGHTWEIGHT_MODE`, `ADO2GH_AUTH_ENABLED`, `ACCELERATOR_URL`, profile vars per `contracts/local-profiles.md`
- [x] T005 [P] Add `.vscode/mcp.json.example` template per `contracts/ide-setup.md`
- [x] T006 [P] Add `services/agent/` and `ado2gh/agents/local/` to pytest-cov scope in `pyproject.toml` with `--cov-fail-under=85` for scoped modules

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared tool catalog, profile loader, audit bridge, stub LLM, lightweight accelerator mode — **blocks all user stories**

**⚠️ CRITICAL**: No user story work until this phase is complete

### Tests (Foundational)

- [x] T007 [P] Add `tests/test_local_profiles.py` for profile YAML load, env overrides, validation rules per `data-model.md`
- [x] T008 [P] Add `tests/test_mcp_tool_catalog.py` for allowlist parity, dry_run defaults, unknown tool rejection
- [x] T009 [P] Add `tests/contract/test_ide_contracts.py` scaffolding against `specs/003-local-agent-ide/contracts/`

### Implementation (Foundational)

- [x] T010 Implement `ado2gh/agents/local/tool_catalog.py` — ToolContract dataclass, full catalog from `contracts/mcp-tool-catalog.md`, executor allowlist
- [x] T011 Implement `ado2gh/agents/local/profiles.py` — `LocalAgentProfile` loader from `config/local-profiles.yaml` + env overrides
- [x] T012 Implement `ado2gh/agents/local/stub_llm.py` — deterministic planner responses; wire `get_llm_provider()` in `ado2gh/agents/llm_provider.py` to honor `LLM_PROVIDER=stub` default
- [x] T013 Implement `ado2gh/agents/local/audit_bridge.py` — write `audit_events` via `ado2gh/assignments/audit.py` with `session_id`, redaction (CA-003, CA-004)
- [x] T014 Refactor `services/agent/mcp_server.py` to import tools from `ado2gh/agents/local/tool_catalog.py` (no duplicate TOOLS dict)
- [x] T015 Add `ADO2GH_LIGHTWEIGHT_MODE` inline job stub in `services/accelerator_api/main.py` or `ado2gh/core/orchestration/` for synchronous dry-run jobs when worker absent
- [x] T016 [P] Add skill front-matter `spec:` refs to `ado2gh/agents/skills/planner.md`, `executor.md`, `validator.md` pointing to `specs/003-local-agent-ide/spec.md`

**Checkpoint**: Foundation ready — shared catalog, profiles, audit, lightweight job path

---

## Phase 3: User Story 1 — Run Migration Agent Locally (Priority: P1) 🎯 MVP

**Goal**: Developer starts local stack, invokes agent HTTP service, receives PEV dry-run outcomes without cloud dependencies.

**Independent Test**: `quickstart.md` Scenario 1 — lightweight profile → `POST /v1/sessions` → dry-run plan/validate response; approval blocks live mutations.

### Tests for User Story 1

- [x] T017 [P] [US1] Add `tests/test_agent_ide_sessions.py` for session lifecycle, dry_run default, approval gate (CA-001, CA-002)
- [x] T018 [P] [US1] Add contract tests for `GET /health` and `POST /v1/sessions` in `tests/contract/test_ide_contracts.py` per `contracts/agent-ide-api.md`

### Implementation for User Story 1

- [x] T019 [US1] Implement `GET /health` in `services/agent/main.py` with `accelerator_reachable`, profile, `llm_provider`, structured `remediation_steps` per `contracts/agent-ide-api.md`
- [x] T020 [US1] Wire profile-aware config on agent startup in `services/agent/main.py` using `ado2gh/agents/local/profiles.py`
- [x] T021 [US1] Extend `POST /v1/sessions` in `services/agent/main.py` to accept `profile_id`, run planner subagent via `ado2gh/agents/planner.py` with stub LLM
- [x] T022 [US1] Implement PEV orchestration in `services/agent/main.py` — planning → executing → validating states per `data-model.md` IDESession transitions
- [x] T023 [US1] Implement `GET /v1/sessions/{session_id}` with role-attributed `messages`, `plan_id`, `run_id`, approval state per contract
- [x] T024 [US1] Implement `POST /v1/sessions/{session_id}/request-live` and `POST /v1/sessions/{session_id}/approve` with CA-002 enforcement
- [x] T025 [US1] Implement `POST /v1/sessions/{session_id}/message` for continued chat steps with stub/cloud LLM in `services/agent/main.py`
- [x] T026 [US1] Implement `GET /v1/llm/status` endpoint in `services/agent/main.py` per `contracts/agent-ide-api.md`
- [x] T027 [US1] Emit audit events on session start, approve, and executor steps via `ado2gh/agents/local/audit_bridge.py` (FR-015)
- [x] T028 [US1] Ensure executor subagent in `ado2gh/agents/executor.py` uses tool catalog allowlist only (FR-007)

**Checkpoint**: US1 — HTTP session dry-run PEV path works locally with stub LLM and audit

---

## Phase 4: User Story 2 — Spec Kit Workflow for Agent Features (Priority: P1)

**Goal**: Spec Kit pipeline governs agent changes; skills and rules reference spec/plan paths traceably.

**Independent Test**: `.cursor/skills/ado2gh-local-agent` exists; skills have `spec:` refs; `/speckit-analyze` on `specs/003-local-agent-ide` finds traceable links (SC-004).

### Tests for User Story 2

- [x] T029 [P] [US2] Add `tests/test_agent_skills_traceability.py` — verify skill markdown files contain `spec:` front-matter to `specs/003-local-agent-ide/spec.md`

### Implementation for User Story 2

- [x] T030 [P] [US2] Create `.cursor/rules/local-agent.mdc` with profiles, guardrails CA-001–CA-004, link to `specs/003-local-agent-ide/quickstart.md`
- [x] T031 [P] [US2] Create `.cursor/skills/ado2gh-local-agent/SKILL.md` — start stack, dry-run session, MCP tools, Spec Kit workflow steps
- [x] T032 [US2] Document Spec Kit agent feature workflow in `specs/003-local-agent-ide/quickstart.md` Scenario 5 cross-links to skills paths (FR-003, FR-011)
- [x] T033 [US2] Add `AGENTS.md` or `README` section linking agent development to Spec Kit commands and `specs/003-local-agent-ide/` artifacts

**Checkpoint**: US2 — Cursor rules/skills discoverable; traceability from spec to skills

---

## Phase 5: User Story 3 — IDE Integration (Cursor, VS Code) (Priority: P1)

**Goal**: Developers invoke agent from Cursor or VS Code via MCP tools or HTTP; approved tools only.

**Independent Test**: `quickstart.md` Scenarios 2 and 6 — `tools/list` and `tools/call` via MCP; VS Code mcp.json example works.

### Tests for User Story 3

- [x] T034 [P] [US3] Extend `tests/test_mcp_tool_catalog.py` for stdio `tools/list` and `tools/call` via `services/agent/mcp_server.py`
- [x] T035 [P] [US3] Add MCP contract tests in `tests/contract/test_ide_contracts.py` per `contracts/mcp-tool-catalog.md`

### Implementation for User Story 3

- [x] T036 [US3] Enhance `services/agent/mcp_server.py` — tool metadata (`subagent`, `requires_approval`), structured connection errors with `remediation_steps` (FR-010)
- [x] T037 [US3] Emit audit events on MCP `tools/call` mutations via `ado2gh/agents/local/audit_bridge.py` (FR-015)
- [x] T038 [US3] Default `dry_run: true` on executor MCP tools when argument omitted per `contracts/mcp-tool-catalog.md`
- [x] T039 [P] [US3] Finalize `.vscode/mcp.json.example` with absolute-path placeholders and env block per `contracts/ide-setup.md`
- [x] T040 [US3] Add gitignore entry for `.vscode/mcp.json` in `.gitignore` if not present (FR-012)
- [x] T041 [US3] Document Cursor MCP user config snippet in `specs/003-local-agent-ide/contracts/ide-setup.md` (verify matches implementation)

**Checkpoint**: US3 — MCP bridge + VS Code template; same catalog as HTTP path

---

## Phase 6: User Story 4 — Pluggable Local Agent Host (Priority: P2)

**Goal**: Alternate local hosts consume versioned contract; capability matrix shows degraded modes.

**Independent Test**: `quickstart.md` — health returns capability matrix; contract tests pass; two hosts (HTTP curl + MCP stdio) produce equivalent dry-run outcomes (SC-003).

### Tests for User Story 4

- [x] T042 [P] [US4] Add `tests/test_local_host_parity.py` — same dry-run plan via HTTP session vs MCP `ado2gh_plan_phase` yields equivalent guardrails
- [x] T043 [P] [US4] Extend `tests/contract/test_ide_contracts.py` for capability matrix fields on `/health`

### Implementation for User Story 4

- [x] T044 [US4] Add `capabilities` / degraded-mode matrix to `GET /health` in `services/agent/main.py` per profile (`enqueue_job` inline vs async) per `contracts/mcp-tool-catalog.md`
- [x] T045 [US4] Add `audit_event_id` to MCP `tools/call` response when audit written per contract
- [x] T046 [US4] Document alternate host requirements and capability matrix in `specs/003-local-agent-ide/contracts/agent-ide-api.md` § Alternate hosts (FR-006)
- [x] T047 [US4] Add version field `tool_catalog_version` to health response sourced from `ado2gh/agents/local/tool_catalog.py`

**Checkpoint**: US4 — external hosts can discover versioned contract and degraded modes

---

## Phase 7: User Story 5 — Lightweight Local Mode (Priority: P3)

**Goal**: Minimal profile (accel + SQLite, no Redis/worker) for prompt/skill iteration; scripts for one-command start.

**Independent Test**: `quickstart.md` Scenario 1 with `docker-compose.lightweight.yml` or `run-local-agent` scripts; no Redis required; planner/validator run against real StateDB.

### Tests for User Story 5

- [x] T048 [P] [US5] Add integration test in `tests/test_lightweight_mode.py` — accelerator starts without redis; inline job completes synchronously
- [x] T049 [P] [US5] Add `scripts/ide-check.ps1` smoke test assertions (health, tools/list, session dry-run) as pytest or documented script exit codes

### Implementation for User Story 5

- [x] T050 [US5] Create `scripts/run-local-agent.ps1` — lightweight accel + agent only per `contracts/local-profiles.md` (Windows primary)
- [x] T051 [P] [US5] Create `scripts/run-local-agent.sh` — macOS/Linux equivalent of `scripts/run-local-agent.ps1`
- [x] T052 [US5] Create `scripts/ide-check.ps1` — smoke: `/health`, MCP `tools/list`, `POST /v1/sessions` dry-run per `quickstart.md`
- [x] T053 [US5] Verify `docker-compose.lightweight.yml` builds and starts without redis/worker services (T003 follow-up validation)
- [x] T054 [US5] Label degraded LLM/queue behavior in `.cursor/skills/ado2gh-local-agent/SKILL.md` for lightweight profile (FR-009)

**Checkpoint**: US5 — one-command lightweight start; documented degraded optional services

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Auth forwarding, coverage gate, quickstart validation, security hardening

- [x] T055 [P] Forward `Cookie` / `ADO2GH_SESSION_COOKIE` from agent to accelerator when `ADO2GH_AUTH_ENABLED=true` in `services/agent/main.py` (FR-013, coordination with `002-login-bootstrap`)
- [x] T056 [P] Add prod-like profile smoke notes to `specs/003-local-agent-ide/quickstart.md` Scenario 8 when auth module exists
- [x] T057 Verify no secrets in audit payloads — extend `tests/test_audit_redaction.py` or add `tests/test_ide_audit_redaction.py` for IDE session/tool audit paths (SC-005)
- [x] T058 [P] Session isolation test — concurrent `session_id` values do not share messages/credentials in `tests/test_agent_ide_sessions.py`
- [x] T059 Run full `quickstart.md` validation checklist and fix gaps in docs or code
- [x] T060 Confirm pytest-cov ≥85% on `ado2gh/agents/local/` and `services/agent/` modules in CI per `pyproject.toml`
- [x] T061 [P] Update root `README.md` with Local Agent IDE section linking to `specs/003-local-agent-ide/quickstart.md`
- [x] T062 Deprecation notice in `scripts/run-local.ps1` pointing to `scripts/run-local-agent.ps1` for agent-only lightweight dev (plan III)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **User Stories (Phase 3–7)**: All depend on Foundational completion
  - US1 (P1 MVP) should complete before US4 parity tests ideally share session code
  - US2 and US3 can parallelize after Foundational (different files)
  - US5 depends on lightweight Compose/scripts from Setup + T015 inline jobs
- **Polish (Phase 8)**: Depends on US1–US3 minimum; full polish after all stories

### User Story Dependencies

| Story | Priority | Depends on | Independent test |
|-------|----------|------------|------------------|
| US1 | P1 | Foundational | `quickstart.md` Scenario 1, 4 |
| US2 | P1 | Foundational | Scenario 5, skill traceability tests |
| US3 | P1 | Foundational, T010 catalog | Scenarios 2, 6 |
| US4 | P2 | US1 + US3 paths | Scenario parity HTTP vs MCP |
| US5 | P3 | T015 lightweight jobs, T003 Compose | Scenario 1 lightweight, docker-compose.lightweight |

### Within Each User Story

- Tests written first (constitution) — should FAIL before implementation
- Foundational catalog before MCP/HTTP story work
- Models (`profiles`, `tool_catalog`) before services (`main.py`, `mcp_server.py`)
- Core endpoints before polish/auth forwarding

### Parallel Opportunities

- **Phase 1**: T002, T003, T004, T005, T006 in parallel after T001
- **Phase 2**: T007, T008, T009 tests in parallel; T016 parallel with T010–T015
- **After Foundational**: US2 (rules/skills) and US3 (MCP polish) parallel with US1 HTTP work if different owners
- **Phase 8**: T055, T056, T057, T061 marked [P]

---

## Parallel Example: User Story 1

```bash
# Tests first (parallel):
pytest tests/test_agent_ide_sessions.py tests/contract/test_ide_contracts.py -k "health or session"

# Implementation split (after T019 health):
# Developer A: T021–T025 session + approval flow in services/agent/main.py
# Developer B: T027–T028 audit + executor allowlist in ado2gh/agents/
```

---

## Parallel Example: User Story 3

```bash
# Parallel after catalog exists:
# Task T039: .vscode/mcp.json.example
# Task T034: tests/test_mcp_tool_catalog.py stdio tests
# Task T036: services/agent/mcp_server.py enhancements
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (**CRITICAL**)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: `quickstart.md` Scenario 1 + Scenario 4 (dry-run / approval)
5. Demo dry-run PEV session from curl or Cursor skill

### Incremental Delivery

1. Setup + Foundational → shared catalog and profiles ready
2. US1 → local HTTP agent MVP (**deploy/demo**)
3. US2 + US3 → IDE discoverability + MCP (P1 complete)
4. US4 → host parity and versioned contract (P2)
5. US5 → lightweight scripts and Compose (P3)
6. Polish → auth, coverage, quickstart sign-off

### Suggested MVP Scope

**User Story 1 only** (Phase 1 + 2 + 3): ~28 tasks (T001–T028). Delivers core value: local dry-run agent sessions with guardrails and audit.

---

## Notes

- `[P]` = parallel-safe (different files, no ordering dependency on incomplete tasks)
- `[USn]` maps to `spec.md` user stories 1–5
- `services/agent/` is tested via pytest importing `services.agent.main` app or httpx AsyncClient
- Do not commit `.env`, `.vscode/mcp.json`, or PATs (FR-012)
- Coordination with `002-login-bootstrap`: T055/T056 are polish until auth module lands; lightweight profile skips auth

---

## Task Summary

| Phase | Story | Task IDs | Count |
|-------|-------|----------|-------|
| Setup | — | T001–T006 | 6 |
| Foundational | — | T007–T016 | 10 |
| US1 Run Locally | P1 MVP | T017–T028 | 12 |
| US2 Spec Kit | P1 | T029–T033 | 5 |
| US3 IDE Integration | P1 | T034–T041 | 8 |
| US4 Pluggable Host | P2 | T042–T047 | 6 |
| US5 Lightweight | P3 | T048–T054 | 7 |
| Polish | — | T055–T062 | 8 |
| **Total** | | **T001–T062** | **62** |
