import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { runCodex, startBridge } from './codex-mcp-bridge.mjs';

function client(t, consult = async () => 'Review complete.') {
  const input = new PassThrough();
  const output = new PassThrough();
  const pending = new Map();
  const messages = [];
  let id = 0;
  let buffer = '';
  output.setEncoding('utf8');
  output.on('data', (chunk) => {
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const message = JSON.parse(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      messages.push(message);
      pending.get(message.id)?.(message);
      pending.delete(message.id);
    }
  });
  const server = startBridge({ input, output, consult });
  t.after(() => { server.close(); input.destroy(); output.destroy(); });
  return {
    input, messages,
    notify(method, params) { input.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`); },
    call(method, params) {
      const requestId = ++id;
      const response = new Promise((resolve) => pending.set(requestId, resolve));
      input.write(`${JSON.stringify({ jsonrpc: '2.0', id: requestId, method, params })}\n`);
      return response;
    },
    initialize(version = '2025-06-18') { return this.call('initialize', { protocolVersion: version }); },
  };
}

test('negotiates supported protocol versions and advertises a read-only consultation', async (t) => {
  const c = client(t);
  assert.equal((await c.call('tools/list')).error.code, -32002);
  for (const version of ['2024-11-05', '2025-03-26', '2025-06-18']) {
    assert.equal((await c.initialize(version)).result.protocolVersion, version);
  }
  assert.equal((await c.initialize('future-version')).result.protocolVersion, '2025-06-18');
  c.notify('notifications/initialized');
  const { tools } = (await c.call('tools/list')).result;
  assert.deepEqual(tools.map((tool) => tool.name), ['ask_chatgpt']);
  assert.equal(tools[0].annotations.readOnlyHint, true);
  assert.deepEqual(tools[0].inputSchema.required, ['prompt']);
  assert.equal(tools[0].inputSchema.additionalProperties, false);
  assert.deepEqual((await c.call('ping')).result, {});
  assert.equal(c.messages.length, 7);
});

test('validates JSON-RPC and tool arguments without invoking the model', async (t) => {
  let invocations = 0;
  const c = client(t, async () => { invocations++; return 'Unexpected'; });
  c.input.write('{broken}\n[]\n');
  assert.deepEqual(c.messages.map((message) => message.error.code), [-32700, -32600]);
  await c.initialize();
  assert.equal((await c.call('unknown')).error.code, -32601);
  for (const args of [{}, { prompt: ' ' }, { prompt: 42 }, { prompt: 'x', cwd: '' },
    { prompt: 'x', sandbox: 'danger-full-access' }, { prompt: 'x'.repeat(65_537) }]) {
    const reply = await c.call('tools/call', { name: 'ask_chatgpt', arguments: args });
    assert.equal(reply.error.code, -32602);
  }
  c.notify('tools/call', { name: 'ask_chatgpt', arguments: { prompt: 'Do not run' } });
  assert.equal(invocations, 0);
});

test('returns final text and represents execution failures as tool errors', async (t) => {
  const c = client(t, async (args) => {
    assert.equal(args.cwd, 'repository');
    if (args.prompt === 'fail') throw new Error('CLI is unavailable.');
    return 'A specific review finding.';
  });
  await c.initialize();
  const success = await c.call('tools/call', {
    name: 'ask_chatgpt', arguments: { prompt: 'review', cwd: 'repository' },
  });
  assert.deepEqual(success.result, { content: [{ type: 'text', text: 'A specific review finding.' }] });
  const failure = await c.call('tools/call', {
    name: 'ask_chatgpt', arguments: { prompt: 'fail', cwd: 'repository' },
  });
  assert.equal(failure.result.isError, true);
  assert.equal(failure.result.content[0].text, 'CLI is unavailable.');
});

test('cancellation and EOF abort active work; concurrent requests are bounded', async (t) => {
  let aborted = 0;
  const c = client(t, (_, signal) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => { aborted++; reject(new Error('Cancelled')); }, { once: true });
  }));
  await c.initialize();
  const first = c.call('tools/call', { name: 'ask_chatgpt', arguments: { prompt: 'wait' } });
  const busy = await c.call('tools/call', { name: 'ask_chatgpt', arguments: { prompt: 'wait' } });
  assert.equal(busy.error.code, -32000);
  c.notify('notifications/cancelled', { requestId: 2 });
  assert.equal((await first).result.isError, true);
  assert.equal(aborted, 1);
  void c.call('tools/call', { name: 'ask_chatgpt', arguments: { prompt: 'wait' } });
  c.input.end();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(aborted, 2);
});

test('oversized unfinished input is rejected before parsing', (t) => {
  const c = client(t);
  c.input.write('x'.repeat(1_048_577));
  assert.equal(c.messages[0].error.code, -32600);
});

async function fakeCli(t) {
  const cwd = await mkdtemp(join(tmpdir(), 'codex-mcp-test-'));
  const script = join(cwd, 'fake-cli.mjs');
  await writeFile(script, `
import { writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
let prompt = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => prompt += chunk);
process.stdin.on('end', () => {
  const args = process.argv.slice(2);
  if (prompt.endsWith('hang')) {
    const descendant = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
      stdio: 'ignore', shell: false, windowsHide: true,
    });
    writeFileSync(join(process.cwd(), 'pids.json'), JSON.stringify([process.pid, descendant.pid]));
    setInterval(() => {}, 1000);
    return;
  }
  if (prompt.endsWith('fail')) { process.stderr.write('private diagnostic'); process.exit(7); }
  const output = args[args.indexOf('--output-last-message') + 1];
  writeFileSync(output, JSON.stringify({ args, prompt, cwd: process.cwd(), delegated: process.env.ADO2GH_MCP_DELEGATED }));
  process.stdout.write('Progress must never appear in the MCP response.');
});
`);
  t.after(() => rm(cwd, { recursive: true, force: true }));
  return { cwd, options: { command: process.execPath, commandArgs: [script] } };
}

async function waitForFile(path) {
  for (let attempts = 0; attempts < 100; attempts++) {
    try { return await readFile(path, 'utf8'); } catch {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }
  throw new Error(`Process fixture did not create ${path}`);
}

test('runs the CLI with inherited model settings, review constraints, and a private reply file', async (t) => {
  const { cwd, options } = await fakeCli(t);
  const response = JSON.parse(await runCodex({ prompt: 'review', cwd }, undefined, options));
  assert.equal(response.cwd.toLowerCase(), cwd.toLowerCase());
  assert.equal(response.delegated, '1');
  assert.equal(response.args[0], 'exec');
  assert.equal(response.args[response.args.indexOf('--sandbox') + 1], 'read-only');
  assert.ok(response.args.includes('--ephemeral'));
  assert.ok(response.args.includes('mcp_servers.claude-code.enabled=false'));
  assert.equal(response.args.includes('--model'), false);
  assert.match(response.prompt, /Do not delegate/);
  assert.ok(response.prompt.endsWith('review'));
  const replyPath = response.args[response.args.indexOf('--output-last-message') + 1];
  await assert.rejects(stat(replyPath), { code: 'ENOENT' });
  await assert.rejects(runCodex({ prompt: 'fail', cwd }, undefined, options), (error) => {
    assert.match(error.message, /code 7/);
    assert.doesNotMatch(error.message, /private diagnostic/);
    return true;
  });
});

test('cancellation terminates the spawned CLI and its descendant', { timeout: 10_000 }, async (t) => {
  const { cwd, options } = await fakeCli(t);
  const controller = new AbortController();
  const running = runCodex({ prompt: 'hang', cwd }, controller.signal, options);
  const rejected = assert.rejects(running, /cancelled/);
  const pids = JSON.parse(await waitForFile(join(cwd, 'pids.json')));
  controller.abort();
  await rejected;
  for (const pid of pids) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
});

test('timeouts stop a stalled CLI', { timeout: 10_000 }, async (t) => {
  const { cwd, options } = await fakeCli(t);
  await assert.rejects(runCodex({ prompt: 'hang', cwd }, undefined, { ...options, timeoutMs: 500 }), /timed out/);
  const pids = JSON.parse(await readFile(join(cwd, 'pids.json'), 'utf8'));
  for (const pid of pids) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
});

test('delegated processes cannot recursively consult Codex', async () => {
  const previous = process.env.ADO2GH_MCP_DELEGATED;
  process.env.ADO2GH_MCP_DELEGATED = '1';
  try {
    await assert.rejects(runCodex({ prompt: 'review' }), /Recursive agent consultation/);
  } finally {
    if (previous === undefined) delete process.env.ADO2GH_MCP_DELEGATED;
    else process.env.ADO2GH_MCP_DELEGATED = previous;
  }
});
