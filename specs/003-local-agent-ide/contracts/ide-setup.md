# Contract: IDE Setup

**Feature**: `003-local-agent-ide`  
**Reference IDE**: Cursor | **Secondary**: VS Code

## Cursor

### Repository artifacts

| Path | Purpose |
|------|---------|
| `.cursor/rules/local-agent.mdc` | Always-on context: profiles, guardrails, quickstart link |
| `.cursor/skills/ado2gh-local-agent/SKILL.md` | Workflows: start stack, dry-run session, MCP tools |
| `.cursor/skills/speckit-*` | Spec Kit pipeline for agent features |

### Setup steps

1. Clone repo and install Python deps: `pip install -e .`
2. Copy `.env.example` → `.env` (never commit)
3. Run lightweight stack: `scripts/run-local-agent.ps1` (Windows) or `scripts/run-local-agent.sh`
4. Open repo in Cursor — rules and skills auto-discover
5. Agent chat: use skill **ado2gh-local-agent** or ask to "start dry-run migration session"

### Cursor MCP (optional direct tools)

Add to Cursor MCP settings (user-level or project):

```json
{
  "mcpServers": {
    "ado2gh": {
      "command": "python",
      "args": ["-m", "services.agent.mcp_server"],
      "env": {
        "ACCELERATOR_URL": "http://localhost:8080",
        "ADO2GH_STORAGE_BACKEND": "sqlite",
        "ADO2GH_SQLITE_PATH": "<repo>/migration_state.db"
      }
    }
  }
}
```

Replace `<repo>` with absolute path.

## VS Code

### Repository artifacts

| Path | Purpose |
|------|---------|
| `.vscode/mcp.json.example` | Template MCP server config |
| `specs/003-local-agent-ide/quickstart.md` | Runnable validation |

### Setup steps

1. Same Python/env setup as Cursor
2. Copy `.vscode/mcp.json.example` → `.vscode/mcp.json` (gitignored) with absolute paths
3. Install an MCP-capable agent extension (e.g. GitHub Copilot agent mode with MCP support)
4. Start local stack via scripts
5. Invoke tools from agent panel per extension docs

### HTTP session alternative

Extensions without MCP can call agent HTTP directly:

```bash
curl -s -X POST http://localhost:8090/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"profile_id":"lightweight","prompt":"Plan POC dry-run","dry_run":true}'
```

## Alternate local hosts

Third-party hosts MUST:

1. Read [mcp-tool-catalog.md](./mcp-tool-catalog.md) or [agent-ide-api.md](./agent-ide-api.md)
2. Honor dry-run defaults and approval semantics
3. Not add tools outside the published catalog
4. Display capability matrix when profile is `lightweight`

## OS notes

| OS | Primary script | Notes |
|----|----------------|-------|
| Windows | `scripts/run-local-agent.ps1` | Uses `npm.cmd` pattern from `run-local.ps1` |
| macOS/Linux | `scripts/run-local-agent.sh` | `uvicorn` on 8080/8090 |
| All | `docker compose -f docker-compose.lightweight.yml up` | Optional containerized lightweight |

## Security

- Never commit `.env`, `.vscode/mcp.json`, or PATs
- Use `ADO2GH_AUTH_ENABLED=true` only for prod-like testing
- Clear session cookies after prod-like tests
