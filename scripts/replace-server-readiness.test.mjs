import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { replaceServerChild } from './replace-server-child.mjs';

test('readiness failure cleans up a real candidate before restoring the previous launch', async () => {
  let candidate;
  const calls = [];
  const snapshot = Object.freeze({ previous: 'old', candidate: 'new' });
  const failure = Error('candidate not ready');
  await assert.rejects(replaceServerChild({
    prepare: async () => snapshot,
    stop: async () => calls.push('old stopped'),
    start: async value => {
      assert.equal(value, snapshot);
      candidate = spawn(process.execPath, ['-e', 'process.send("started"); setInterval(() => {}, 1000)'], {
        stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
      });
      await once(candidate, 'message');
      calls.push('candidate started');
    },
    ready: async value => {
      assert.equal(value, snapshot);
      assert.equal(process.kill(candidate.pid, 0), true);
      throw failure;
    },
    cleanup: async (value, error) => {
      assert.equal(value, snapshot);
      assert.equal(error, failure);
      const exited = once(candidate, 'exit');
      candidate.kill('SIGTERM');
      await exited;
      calls.push('candidate stopped');
    },
    recover: async () => {
      assert.equal(candidate.signalCode, 'SIGTERM');
      calls.push('previous restored');
    },
  }), error => error === failure);
  assert.deepEqual(calls, ['old stopped', 'candidate started', 'candidate stopped', 'previous restored']);
});

test('uncertain cleanup retains both errors and prevents recovery', async () => {
  const failure = Error('not ready');
  const cleanupFailure = Error('termination uncertain');
  await assert.rejects(replaceServerChild({
    prepare: async () => ({}), stop: async () => {}, start: async () => {},
    ready: async () => { throw failure; },
    cleanup: async () => { throw cleanupFailure; },
    recover: async () => assert.fail('must not restore while candidate may be alive'),
  }), error => error instanceof AggregateError && error.errors[0] === failure && error.errors[1] === cleanupFailure);
});

test('successful readiness completes without cleanup or recovery', async () => {
  const calls = [];
  await replaceServerChild({
    prepare: async () => ({}), stop: async () => {}, start: async () => calls.push('start'),
    ready: async () => calls.push('ready'),
    cleanup: async () => assert.fail('healthy candidate must stay alive'),
    recover: async () => assert.fail('healthy candidate must stay alive'),
  });
  assert.deepEqual(calls, ['start', 'ready']);
});

test('partial start failure also cleans up before recovery', async () => {
  const calls = [];
  await assert.rejects(replaceServerChild({
    prepare: async () => ({}), stop: async () => {},
    start: async () => { throw Error('partial spawn'); },
    ready: async () => assert.fail('start did not finish'),
    cleanup: async () => calls.push('cleanup'),
    recover: async () => calls.push('recover'),
  }), /partial spawn/);
  assert.deepEqual(calls, ['cleanup', 'recover']);
});
