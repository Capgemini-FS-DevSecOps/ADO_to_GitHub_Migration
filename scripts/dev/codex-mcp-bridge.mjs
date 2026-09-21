#!/usr/bin/env node
// Small stdio MCP adapter for Codex versions that no longer ship mcp-server.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const VERSIONS = ['2024-11-05', '2025-03-26', '2025-06-18'];
const MAX_PROMPT = 65_536;
const MAX_MESSAGE = 1_048_576;
const MAX_REPLY = 2_097_152;
const REVIEW_INSTRUCTIONS = `You are providing a read-only consultation to Claude Code.
Inspect and explain only. Do not edit files, change settings, execute migrations,
or take other mutating actions. Do not delegate to another agent or invoke any
Claude Code, ChatGPT, or Codex MCP bridge. Return your findings directly.
The consultation request follows:\n\n`;

async function resolveCodexCommand() {
  if (process.env.CODEX_CLI_PATH) return process.env.CODEX_CLI_PATH;
  if (process.platform !== 'win32') return 'codex';
  for (const directory of (process.env.PATH || '').split(delimiter).filter(Boolean)) {
    const candidate = join(directory.replace(/^"|"$/g, ''), 'codex.exe');
    if (await stat(candidate).then((info) => info.isFile(), () => false)) return candidate;
  }
  // Codex desktop installs versioned native binaries without a stable PATH shim.
  if (process.env.LOCALAPPDATA) {
    const root = join(process.env.LOCALAPPDATA, 'OpenAI', 'Codex', 'bin');
    const entries = await readdir(root, { withFileTypes: true }).catch(() => []);
    const candidates = await Promise.all(entries.filter((entry) => entry.isDirectory()).map(async (entry) => {
      const path = join(root, entry.name, 'codex.exe');
      const info = await stat(path).catch(() => null);
      return info?.isFile() ? { path, updated: info.mtimeMs } : null;
    }));
    const latest = candidates.filter(Boolean).sort((a, b) => b.updated - a.updated)[0];
    if (latest) return latest.path;
  }
  throw new Error('Cannot find native codex.exe. Install Codex or set CODEX_CLI_PATH.');
}

// Only target the process we spawned and its descendants, never an executable name.
export function terminateTree(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32') {
    const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      shell: false, windowsHide: true, stdio: 'ignore',
    });
    const fallback = setTimeout(() => {
      killer.kill();
      child.kill();
    }, 3_000);
    killer.once('error', () => child.kill());
    killer.once('close', (code) => {
      clearTimeout(fallback);
      if (code !== 0) child.kill();
    });
  } else {
    try { process.kill(-child.pid, 'SIGTERM'); } catch { child.kill(); }
    const force = setTimeout(() => {
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* Already exited. */ }
    }, 500);
    child.once('close', () => clearTimeout(force));
  }
}

export async function runCodex({ prompt, cwd = process.cwd() }, signal, {
  command,
  commandArgs = [],
  timeoutMs = 300_000,
} = {}) {
  if (process.env.ADO2GH_MCP_DELEGATED === '1') {
    throw new Error('Recursive agent consultation is disabled. Return your answer directly.');
  }
  signal?.throwIfAborted();
  command ||= await resolveCodexCommand();
  const workdir = resolve(cwd);
  if (!(await stat(workdir)).isDirectory()) throw new Error('cwd must be a directory.');
  const temp = await mkdtemp(join(tmpdir(), 'codex-mcp-'));
  const replyPath = join(temp, 'reply.txt');
  try {
    signal?.throwIfAborted();
    const args = [...commandArgs, 'exec', '--sandbox', 'read-only', '--ephemeral',
      '--color', 'never', '-c', 'mcp_servers.claude-code.enabled=false',
      '-c', 'mcp_servers.claude-consult.enabled=false',
      '--output-last-message', replyPath, '-'];
    await new Promise((fulfill, reject) => {
      const child = spawn(command, args, {
        cwd: workdir, env: { ...process.env, ADO2GH_MCP_DELEGATED: '1' },
        shell: false, windowsHide: true, detached: process.platform !== 'win32',
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      let stopped;
      let grace;
      let finished = false;
      const finish = (error) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        clearTimeout(grace);
        signal?.removeEventListener('abort', cancel);
        if (error) reject(error); else fulfill();
      };
      const stop = (error) => {
        if (finished || stopped) return;
        stopped = error;
        terminateTree(child);
        grace = setTimeout(() => {
          child.stdin.destroy();
          child.stdout.destroy();
          child.stderr.destroy();
          child.kill('SIGKILL');
          finish(error);
        }, 5_000);
      };
      const cancel = () => stop(new Error('Consultation cancelled.'));
      const timer = setTimeout(() => stop(new Error('Codex consultation timed out.')), timeoutMs);
      signal?.addEventListener('abort', cancel, { once: true });
      child.once('error', (error) => finish(new Error(`Cannot launch Codex: ${error.message}`)));
      child.once('close', (code) => finish(stopped || (code === 0 ? undefined :
        new Error(`Codex exited with code ${code}. Check Codex login status and CLI settings.`))));
      child.stdout.resume(); // CLI progress is not part of the MCP transport.
      child.stderr.resume(); // CLI diagnostics can contain echoed prompts or credentials.
      child.stdin.on('error', () => {}); // An early CLI exit can close stdin first.
      child.stdin.end(REVIEW_INSTRUCTIONS + prompt);
      if (signal?.aborted) cancel();
    });
    if ((await stat(replyPath)).size > MAX_REPLY) throw new Error('Codex reply exceeds 2 MiB.');
    const reply = (await readFile(replyPath, 'utf8')).trim();
    if (!reply) throw new Error('Codex returned no final reply.');
    return reply;
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}

export function startBridge({
  input = process.stdin, output = process.stdout, consult = runCodex,
  toolName = 'ask_chatgpt', serverName = 'chatgpt-codex-bridge',
  instructions = 'Read-only ChatGPT consultation through the locally authenticated Codex CLI.',
  description = 'Consult ChatGPT through Codex for read-only code review, planning, or debugging. Uses existing Codex login and model settings.',
} = {}) {
  let initialized = false;
  let closed = false;
  let buffer = '';
  const active = new Map();
  const send = (message) => {
    if (!output.destroyed && output.writable) output.write(`${JSON.stringify(message)}\n`);
  };
  const result = (id, value) => send({ jsonrpc: '2.0', id, result: value });
  const error = (id, code, message) => send({ jsonrpc: '2.0', id, error: { code, message } });
  const close = () => {
    closed = true;
    for (const controller of active.values()) controller.abort();
  };
  async function handle(line) {
    let request;
    try { request = JSON.parse(line); } catch { error(null, -32700, 'Invalid JSON.'); return; }
    if (!request || Array.isArray(request) || request.jsonrpc !== '2.0' ||
        typeof request.method !== 'string' ||
        ('id' in request && request.id !== null && !['string', 'number'].includes(typeof request.id))) {
      error(null, -32600, 'Invalid JSON-RPC request.'); return;
    }
    const { id, method, params } = request;
    if (!('id' in request)) {
      if (method === 'notifications/cancelled') active.get(params?.requestId)?.abort();
      return; // Includes notifications/initialized; notifications never get responses.
    }
    if (method === 'initialize') {
      if (!params || typeof params.protocolVersion !== 'string') {
        error(id, -32602, 'protocolVersion is required.'); return;
      }
      initialized = true;
      result(id, {
        protocolVersion: VERSIONS.includes(params.protocolVersion) ? params.protocolVersion : VERSIONS.at(-1),
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: serverName, version: '1.0.0' },
        instructions,
      });
      return;
    }
    if (method === 'ping') { result(id, {}); return; }
    if (!initialized) { error(id, -32002, 'Initialize the MCP server first.'); return; }
    if (method === 'tools/list') {
      result(id, { tools: [{
        name: toolName,
        description,
        inputSchema: {
          type: 'object', additionalProperties: false, required: ['prompt'],
          properties: {
            prompt: { type: 'string', minLength: 1, maxLength: MAX_PROMPT },
            cwd: { type: 'string', minLength: 1, description: 'Repository directory; defaults to the MCP server working directory.' },
          },
        },
        annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
      }] });
      return;
    }
    if (method !== 'tools/call') { error(id, -32601, 'Unknown method.'); return; }
    const args = params?.arguments;
    if (params?.name !== toolName || !args || Array.isArray(args) ||
        typeof args.prompt !== 'string' || !args.prompt.trim() || args.prompt.length > MAX_PROMPT ||
        (args.cwd !== undefined && (typeof args.cwd !== 'string' || !args.cwd.trim())) ||
        Object.keys(args).some((key) => !['prompt', 'cwd'].includes(key))) {
      error(id, -32602, `Expected ${toolName} with prompt and optional cwd.`); return;
    }
    if (active.size) { error(id, -32000, 'A consultation is already running.'); return; }
    const controller = new AbortController();
    active.set(id, controller);
    try {
      const text = await consult(args, controller.signal);
      if (!closed) result(id, { content: [{ type: 'text', text }] });
    } catch (failure) {
      if (!closed) result(id, { isError: true, content: [{ type: 'text', text: String(failure.message || failure) }] });
    } finally {
      active.delete(id);
    }
  }
  input.setEncoding('utf8');
  input.on('data', (chunk) => {
    if (closed) return;
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line.length > MAX_MESSAGE) { error(null, -32600, 'Message too large.'); close(); return; }
      if (line) void handle(line);
    }
    if (buffer.length > MAX_MESSAGE) { error(null, -32600, 'Message too large.'); close(); }
  });
  input.on('end', close);
  input.on('error', close);
  output.on('error', close);
  return { close };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const bridge = startBridge();
  process.once('SIGINT', () => { bridge.close(); process.stdin.destroy(); });
  process.once('SIGTERM', () => { bridge.close(); process.stdin.destroy(); });
}
