#!/usr/bin/env node
// Read-only model consultation; native `claude mcp serve` exposes tools only.
import { spawn } from 'node:child_process';
import { stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startBridge, terminateTree } from './codex-mcp-bridge.mjs';

const MAX_REPLY = 2_097_152;
const REVIEW_INSTRUCTIONS = `This is an already-delegated read-only consultation from Codex.
Inspect and explain only. Do not edit files, change settings, or execute migrations.
Do not delegate or invoke another agent, Claude Code, ChatGPT, or Codex MCP bridge.
Return your findings directly. The consultation request follows:\n\n`;

async function resolveClaudeCommand() {
  if (process.env.CLAUDE_CLI_PATH) return process.env.CLAUDE_CLI_PATH;
  if (process.platform !== 'win32') return 'claude';
  const candidates = [
    join(homedir(), '.local', 'bin', 'claude.exe'),
    ...(process.env.PATH || '').split(delimiter).filter(Boolean)
      .map((directory) => join(directory.replace(/^"|"$/g, ''), 'claude.exe')),
  ];
  for (const candidate of candidates) {
    if (await stat(candidate).then((info) => info.isFile(), () => false)) return candidate;
  }
  throw new Error('Cannot find claude.exe. Set CLAUDE_CLI_PATH.');
}

export async function runClaude({ prompt, cwd = process.cwd() }, signal, {
  command, commandArgs = [], timeoutMs = 300_000,
} = {}) {
  if (process.env.ADO2GH_MCP_DELEGATED === '1') {
    throw new Error('Recursive agent consultation is disabled. Return your answer directly.');
  }
  signal?.throwIfAborted();
  command ||= await resolveClaudeCommand();
  const workdir = resolve(cwd);
  if (!(await stat(workdir)).isDirectory()) throw new Error('cwd must be a directory.');
  signal?.throwIfAborted();
  const args = [...commandArgs,
    '--print', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep',
    '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
    '--no-session-persistence', '--output-format', 'text',
  ];
  return new Promise((fulfill, reject) => {
    const child = spawn(command, args, {
      cwd: workdir,
      env: { ...process.env, ADO2GH_MCP_DELEGATED: '1' },
      shell: false,
      windowsHide: true,
      detached: process.platform !== 'win32',
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const chunks = [];
    let size = 0;
    let stopped;
    let grace;
    let finished = false;
    const finish = (error) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      clearTimeout(grace);
      signal?.removeEventListener('abort', cancel);
      if (error) reject(error);
      else {
        const reply = Buffer.concat(chunks).toString('utf8').trim();
        if (!reply) reject(new Error('Claude CLI returned an empty response.'));
        else fulfill(reply);
      }
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
    const timer = setTimeout(() => stop(new Error('Claude consultation timed out.')), timeoutMs);
    signal?.addEventListener('abort', cancel, { once: true });
    if (signal?.aborted) cancel();
    child.once('error', (error) => finish(new Error(`Could not start Claude CLI: ${error.message}`)));
    child.once('close', (code) => finish(stopped || (code === 0 ? null
      : new Error(`Claude CLI exited with code ${code}. Check claude auth status.`))));
    child.stdout.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_REPLY) stop(new Error('Claude reply exceeded 2 MiB.'));
      else chunks.push(chunk);
    });
    // CLI diagnostics may contain private paths; keep them off MCP stdout.
    child.stderr.resume();
    child.stdin.on('error', () => { /* Exit/error handlers report failures. */ });
    child.stdin.end(REVIEW_INSTRUCTIONS + prompt);
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const bridge = startBridge({
    consult: runClaude,
    toolName: 'ask_claude',
    serverName: 'claude-consult',
    instructions: 'Read-only model consultation through the locally authenticated Claude Code CLI.',
    description: 'Consult Claude for read-only code review, planning, or debugging. Uses the existing Claude login and model settings.',
  });
  process.once('SIGINT', () => { bridge.close(); process.stdin.destroy(); });
  process.once('SIGTERM', () => { bridge.close(); process.stdin.destroy(); });
}
