# Claude Code and ChatGPT / Codex MCP

User-level MCP registrations connect the local coding agents:

| Client | Server | Implementation |
| --- | --- | --- |
| Claude Code | `chatgpt` | Node runs `scripts/dev/codex-mcp-bridge.mjs`; `ask_chatgpt` runs the signed-in Codex CLI in a read-only sandbox. |
| Codex | `claude-consult` | Node runs `scripts/dev/claude-mcp-bridge.mjs`; `ask_claude` runs the signed-in Claude CLI with read tools only. |
| Codex | `claude-code` | The installed Claude executable runs its native `mcp serve` tool service. |

The registrations live in `%USERPROFILE%/.claude.json` and
`%USERPROFILE%/.codex/config.toml`, respectively. They use the existing ChatGPT
and Claude logins. No credentials are stored in this repository. The adapter uses
the configured Codex model and provider settings without pinning a model.

These are separate agent calls, not messages to already-open conversations.
Every ChatGPT call needs its own context. The read-only ChatGPT adapter provides
analysis and review; the caller applies changes and runs tests.

## Activate and check

1. Restart Claude Code and restart Codex's MCP connections (or restart the app).
2. In Claude Code, use `/mcp` to check that `chatgpt` is connected. This
   repository's `CLAUDE.md` requires consultation for substantive work.
3. In Codex, use `/mcp` to check that `claude-consult` and `claude-code` are connected.
4. Ask Claude to consult ChatGPT on a small repository question. For the reverse
   direction, ask Codex to consult Claude through the `claude-consult` server.

Claude's tool is normally named `mcp__chatgpt__ask_chatgpt`; pass `prompt` and the
absolute checkout path as `cwd`. It accepts one active consultation per server
and times out after five minutes. It does not expose write or sandbox overrides.

For a Claude response, use `claude-consult` / `ask_claude` with `prompt` and `cwd`.
Its child process can use `Read`, `Glob`, and `Grep`; it has no shell, edit,
delegation, or external MCP tools. Each call is independent and uses Claude's
configured model. Tool availability can be inspected through `/mcp`.

Claude Code 2.1.241's native server advertises `Agent`, but an actual call returns
`Agent type 'general-purpose' not found. Available agents: none`. An explicit
agent definition did not resolve this in native MCP mode. The separate
`claude-consult` adapter supplies working model consultation; `claude-code`
retains the native tool service the user requested.

Both directions mark delegated work with `ADO2GH_MCP_DELEGATED=1`. Delegated
requests must not call back through either bridge. The adapter also disables the
reverse `claude-code` and `claude-consult` MCP servers in its child Codex process.

## Maintenance

- If either account is signed out, use the corresponding CLI's login flow, then
  restart its MCP connection.
- If the repository moves, update the absolute adapter path in Claude's MCP
  registration. Node and the Codex CLI must be installed and discoverable.
- `CODEX_CLI_PATH` can select an explicit Codex executable when PATH resolution
  is unavailable. Avoid permanently pinning an app-managed version directory.
- Diagnose connection errors in `/mcp`. A connected server verifies the MCP
  transport; an actual tool call also checks model access and account limits.
- Run the adapter's offline checks with
  `rtk proxy node --test scripts/dev/codex-mcp-bridge.test.mjs`.

## Why an adapter is needed

The installed Codex CLI no longer hosts an MCP server. OpenAI documents the
[MCP server removal](https://learn.chatgpt.com/docs/mcp-server) and supports
[`codex exec` for non-interactive use](https://learn.chatgpt.com/docs/non-interactive-mode).
The local adapter exposes that CLI operation through MCP. Codex's app-server
protocol is a different protocol and cannot be registered directly as MCP.

Claude still provides a
[native MCP server](https://code.claude.com/docs/en/mcp#use-claude-code-as-an-mcp-server).
Codex loads it through its standard
[MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
