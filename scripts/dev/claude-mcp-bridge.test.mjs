import assert from 'node:assert/strict';
import test from 'node:test';
import { runClaude } from './claude-mcp-bridge.mjs';

const fakeCli = (source, options = {}) => ({
  command: process.execPath,
  commandArgs: ['--input-type=module', '-e', source, '--'],
  ...options,
});

test('Claude consultation restricts tools, isolates MCP, and forwards context over stdin', async () => {
  const reply = JSON.parse(await runClaude({ prompt: 'Review the requested change.' }, undefined,
    fakeCli(`let prompt = ''; process.stdin.setEncoding('utf8');
      process.stdin.on('data', chunk => { prompt += chunk; });
      process.stdin.on('end', () => console.log(JSON.stringify({
        args: process.argv.slice(1), prompt, guard: process.env.ADO2GH_MCP_DELEGATED,
      })));`)));
  assert.equal(reply.guard, '1');
  assert.match(reply.prompt, /already-delegated read-only consultation/);
  assert.ok(reply.prompt.endsWith('Review the requested change.'));
  const option = (name) => reply.args[reply.args.indexOf(name) + 1];
  assert.equal(option('--tools'), 'Read,Glob,Grep');
  assert.equal(option('--allowedTools'), 'Read,Glob,Grep');
  assert.deepEqual(JSON.parse(option('--mcp-config')), { mcpServers: {} });
  assert.ok(reply.args.includes('--strict-mcp-config'));
  assert.ok(reply.args.includes('--no-session-persistence'));
  assert.ok(!reply.args.includes('--model'));
});

test('Claude reports failures without forwarding diagnostics', async () => {
  await assert.rejects(runClaude({ prompt: 'Check.' }, undefined,
    fakeCli(`process.stderr.write('PRIVATE_DIAGNOSTIC'); process.exit(3);`)),
  /Claude CLI exited with code 3/);
  await assert.rejects(runClaude({ prompt: 'Check.' }, undefined,
    fakeCli(`process.stdin.resume();`)), /empty response/);
});

test('Claude consultation times out and cancels hung child processes', async () => {
  const hanging = fakeCli(`setInterval(() => {}, 1000);`, { timeoutMs: 100 });
  await assert.rejects(runClaude({ prompt: 'Check.' }, undefined, hanging), /timed out/);
  const controller = new AbortController();
  const result = runClaude({ prompt: 'Check.' }, controller.signal,
    fakeCli(`setInterval(() => {}, 1000);`));
  const timer = setTimeout(() => controller.abort(), 100);
  try { await assert.rejects(result, /cancelled/); } finally { clearTimeout(timer); }
});

test('Claude rejects recursive delegation before starting a process', async () => {
  const original = process.env.ADO2GH_MCP_DELEGATED;
  process.env.ADO2GH_MCP_DELEGATED = '1';
  try {
    await assert.rejects(runClaude({ prompt: 'Check.' }), /Recursive agent consultation/);
  } finally {
    if (original === undefined) delete process.env.ADO2GH_MCP_DELEGATED;
    else process.env.ADO2GH_MCP_DELEGATED = original;
  }
});
