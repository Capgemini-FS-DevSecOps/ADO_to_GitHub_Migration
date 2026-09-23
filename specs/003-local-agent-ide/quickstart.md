# Quickstart: Local Agent IDE

**Feature**: `003-local-agent-ide`  
**Goal**: Validate SC-001 — new developer dry-run session in under 15 minutes.

**References**: [contracts/agent-ide-api.md](./contracts/agent-ide-api.md), [contracts/mcp-tool-catalog.md](./contracts/mcp-tool-catalog.md), [contracts/ide-setup.md](./contracts/ide-setup.md), [contracts/local-profiles.md](./contracts/local-profiles.md), [data-model.md](./data-model.md)

## Prerequisites

- Python 3.9+ with `pip install -e .`
- Git clone of this repository
- (Optional) Real ADO/GH tokens in `.env` for live API calls — **not required** for stub dry-run path

## Scenario 1: Lightweight profile + HTTP session (P1)

**Proves**: User Story 1, FR-001, FR-002, CA-001

### Setup

```powershell
# Windows (from repo root)
copy .env.example .env
# Edit .env: LLM_PROVIDER=stub, ADO2GH_AUTH_ENABLED=false

pip install -e .
# After implementation: scripts/run-local-agent.ps1
# Interim manual start:
$env:ADO2GH_STORAGE_BACKEND="sqlite"
$env:ADO2GH_SQLITE_PATH="$PWD\migration_state.db"
$env:ADO2GH_LIGHTWEIGHT_MODE="true"
$env:LLM_PROVIDER="stub"
Start-Process powershell -ArgumentList "-NoExit","-Command","python -m uvicorn services.accelerator_api.main:app --port 8080"
Start-Sleep 2
$env:ACCELERATOR_URL="http://localhost:8080"
Start-Process powershell -ArgumentList "-NoExit","-Command","python -m uvicorn services.agent.main:app --port 8090"
```

### Run

```powershell
curl.exe -s http://localhost:8090/health
curl.exe -s -X POST http://localhost:8090/v1/sessions `
  -H "Content-Type: application/json" `
  -d "{\"profile_id\":\"lightweight\",\"prompt\":\"Plan POC dry-run\",\"dry_run\":true}"
```

### Expected

- `/health` → `accelerator_reachable: true`, `llm_provider: stub`
- Session response → `status: planning` or `completed`, `dry_run: true`
- No cloud LLM errors
- `audit_events` row for session start (query StateDB or `/v1/audit` when implemented)

## Scenario 2: MCP tool call (P1)

**Proves**: User Story 3, FR-006, FR-006a

```powershell
$env:ACCELERATOR_URL="http://localhost:8080"
echo '{"method":"tools/list"}' | python -m services.agent.mcp_server
```

### Expected

- JSON listing `ado2gh_discover`, `ado2gh_plan_phase`, etc.
- Tool names match [mcp-tool-catalog.md](./contracts/mcp-tool-catalog.md)

Dry-run plan tool:

```powershell
$line = '{"method":"tools/call","params":{"name":"ado2gh_plan_phase","arguments":{"phase":"poc","dry_run":true}}}'
echo $line | python -m services.agent.mcp_server
```

### Expected

- Accelerator JSON in `content[0].text`
- No shell execution outside allowlist

## Scenario 3: Backend down guidance (edge case)

**Proves**: FR-010, SC-006

1. Stop accelerator process
2. `curl http://localhost:8090/health`

### Expected

- `status: degraded`, `remediation_steps` array with start commands
- IDE skill text references same steps

## Scenario 4: Live mutation blocked (guardrail)

**Proves**: CA-001, CA-002

1. Start session with `dry_run: true`
2. Attempt live enqueue without approval (implementation phase)

### Expected

- Job runs as dry-run OR blocked until `POST .../approve`
- Audit records `denied` or `success` with `dry_run: true`

## Scenario 5: Cursor discoverability (P1)

**Proves**: User Story 2, User Story 3, FR-003, FR-004

1. Open repo in Cursor
2. Confirm `.cursor/rules/local-agent.mdc` and skill `ado2gh-local-agent` exist (post-implementation)
3. Run Spec Kit: `/speckit-analyze` on `specs/003-local-agent-ide`

### Expected

- Agent commands/skills visible in Cursor agent UI
- Analyze report links spec to plan/contracts

## Scenario 6: VS Code alternate IDE (P1)

**Proves**: FR-005, SC-003

1. Copy `.vscode/mcp.json.example` → `.vscode/mcp.json` with absolute paths
2. Configure MCP-capable VS Code extension per [ide-setup.md](./contracts/ide-setup.md)
3. Run Scenario 2 tool list

### Expected

- Same tool catalog as Cursor MCP path

## Scenario 7: Full Compose profile (P3)

**Proves**: FR-009, lightweight vs full difference

```bash
docker compose up --build
```

### Expected

- Redis + worker running; async jobs
- Agent at `8090`, web UI at `3000`

## Scenario 8: Prod-like auth (coordination with 002)

**Proves**: FR-013

Requires `002-login-bootstrap` implementation:

1. `ADO2GH_AUTH_ENABLED=true` on accelerator
2. Bootstrap admin via `/v1/auth/bootstrap`
3. Agent calls with session cookie

### Expected

- Unauthenticated accelerator calls → 401
- Authenticated session → same dry-run behavior

## Cleanup

- Stop uvicorn windows or `docker compose down`
- Do not commit `.env` or `.vscode/mcp.json`

## Success checklist

| ID | Check |
|----|-------|
| SC-001 | Scenarios 1–2 complete in < 15 min |
| SC-002 | Scenario 4 — dry-run default |
| SC-003 | Scenarios 1 and 6 equivalent outcomes |
| SC-004 | Scenario 5 — Spec Kit traceability |
| SC-005 | No tokens in curl output or logs |
| SC-006 | Scenario 3 remediation usable |
