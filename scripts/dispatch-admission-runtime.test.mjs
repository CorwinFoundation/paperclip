import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createDispatchAdmission, withDispatchAdmission } from '../server/src/services/dispatch-admission.ts';

test('closed admission leaves durable queue and sessions untouched', async () => {
  const gate = createDispatchAdmission();
  const queue = ['wake']; const sessions = ['active-session'];
  await gate.drain(0);
  assert.equal(await withDispatchAdmission(gate, async () => queue.pop()), null);
  assert.deepEqual(queue, ['wake']); assert.deepEqual(sessions, ['active-session']);
});

test('drain waits for asynchronous claim publication before active-run observation', async () => {
  const gate = createDispatchAdmission(); let publish;
  let running = 0;
  const pending = withDispatchAdmission(gate, async () => {
    await new Promise(resolve => { publish = resolve; });
    running++; return 'claimed';
  });
  const drained = gate.drain(1000);
  assert.equal(gate.closed, true); assert.equal(gate.active, 1);
  publish(); assert.equal(await pending, 'claimed');
  assert.equal(await drained, true); assert.equal(running, 1);
  // A drained claim barrier is not proof that running adapters finished.
});

test('failed claim releases its lease and normal dispatch can continue', async () => {
  const gate = createDispatchAdmission();
  await assert.rejects(withDispatchAdmission(gate, async () => { throw Error('db failure'); }));
  assert.equal(gate.active, 0);
  assert.equal(await withDispatchAdmission(gate, async () => 'ok'), 'ok');
});

test('both queued-run entry paths use the shared guarded claim', () => {
  const source = readFileSync(new URL('../server/src/services/heartbeat.ts', import.meta.url), 'utf8');
  assert.match(source, /return withDispatchAdmission\(dispatchAdmission, \(\) => claimQueuedRunAdmitted\(run, companyAgents\)\);/);
  assert.equal((source.match(/claimQueuedRunAdmitted\(/g) ?? []).length, 2);
  assert.match(source, /await claimQueuedRun\(queuedRun, companyAgents\)/);
  assert.match(source, /await claimQueuedRun\(run\)/);
});
