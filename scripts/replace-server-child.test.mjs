import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { replaceServerChild } from './replace-server-child.mjs';

async function fixture() {
  const child = spawn(process.execPath, ['-e', 'process.send("ready"); setInterval(() => {}, 1000)'], { stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
  await once(child, 'message');
  return child;
}
async function stop(child) {
  const exited = once(child, 'exit');
  child.kill('SIGTERM');
  await exited;
}

test('failed preparation preserves the real owning server child', async () => {
  const child = await fixture();
  try {
    await assert.rejects(replaceServerChild({
      prepare: async () => { throw Error('SDK build failed'); },
      stop: async () => assert.fail('must preserve current process'),
      start: async () => assert.fail('must not start replacement'),
    }), /SDK build failed/);
    assert.equal(child.exitCode, null);
    assert.equal(child.signalCode, null);
    assert.equal(process.kill(child.pid, 0), true);
  } finally { await stop(child); }
});

test('preparation finishes before old child exits and replacement starts', async () => {
  const old = await fixture();
  let next;
  const calls = [];
  try {
    await replaceServerChild({
      prepare: async () => { assert.equal(process.kill(old.pid, 0), true); calls.push('prepared'); },
      stop: async () => { await stop(old); calls.push('stopped'); },
      start: async () => { assert.equal(old.signalCode, 'SIGTERM'); next = await fixture(); calls.push('started'); },
    });
    assert.deepEqual(calls, ['prepared', 'stopped', 'started']);
    assert.equal(process.kill(next.pid, 0), true);
  } finally {
    if (next) await stop(next);
    if (!old.signalCode) await stop(old);
  }
});

test('stop failure prevents a second server from starting', async () => {
  await assert.rejects(replaceServerChild({ prepare: async () => {}, stop: async () => { throw Error('stop failed'); }, start: async () => assert.fail('must not start') }), /stop failed/);
});
