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

const { createDispatchControl } = await import('../server/src/services/dispatch-admission.ts');

test('operator drain denies restart while running, then status permits it after completion', async () => {
  const gate = createDispatchAdmission(); let running = 1;
  const control = createDispatchControl(gate, async () => running);
  assert.equal((await control.drain(0)).restartReady, false);
  assert.equal(gate.acquire(), null);
  running = 0;
  assert.equal((await control.status()).restartReady, true);
  assert.deepEqual(control.resume(), { closed: false, restartReady: false });
  const release = gate.acquire(); assert.equal(typeof release, 'function'); release();
  assert.equal((await control.status()).restartReady, false);
});

test('resume invalidates pending drain even if admission closes again', async () => {
  const gate = createDispatchAdmission(); const release = gate.acquire();
  const control = createDispatchControl(gate, async () => 0);
  const old = control.drain(1000);
  control.resume();
  const current = control.drain(1000);
  release();
  assert.equal((await old).restartReady, false);
  assert.equal((await current).restartReady, true);
});

test('invalid or unavailable authoritative counts fail closed and allow explicit rollback', async () => {
  for (const count of [undefined, NaN, -1, 0.5, '0']) {
    const gate = createDispatchAdmission();
    const control = createDispatchControl(gate, async () => count);
    await assert.rejects(control.drain(0), /count unavailable/);
    assert.equal(gate.closed, true);
    control.resume(); assert.equal(gate.closed, false);
  }
  const gate = createDispatchAdmission();
  const control = createDispatchControl(gate, async () => { throw Error('database offline'); });
  await assert.rejects(control.drain(0), /database offline/);
  assert.equal(gate.closed, true);
});

test('resume while the authoritative query is pending invalidates its snapshot', async () => {
  const gate = createDispatchAdmission(); let answer;
  const control = createDispatchControl(gate, () => new Promise(resolve => { answer = resolve; }));
  await gate.drain(0);
  const snapshot = control.status();
  control.resume(); await gate.drain(0);
  answer(0);
  assert.equal((await snapshot).restartReady, false);
});
