import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createDispatchAdmission } from './dispatch-admission.mjs';

test('drain closes admission synchronously and waits for all existing leases', async () => {
  const gate = createDispatchAdmission();
  const first = gate.acquire();
  const second = gate.acquire();
  const drained = gate.drain(1000);
  assert.equal(gate.acquire(), null);
  first();
  first();
  assert.equal(gate.active, 1);
  second();
  assert.equal(await drained, true);
  assert.equal(gate.acquire(), null);
});

test('timeout fails closed without cancelling existing work', async () => {
  const gate = createDispatchAdmission();
  const release = gate.acquire();
  assert.equal(await gate.drain(0), false);
  assert.equal(gate.active, 1);
  assert.equal(gate.acquire(), null);
  release();
  assert.equal(await gate.drain(0), true);
});

test('concurrent drains settle independently when outstanding work finishes', async () => {
  const gate = createDispatchAdmission();
  const release = gate.acquire();
  const expired = gate.drain(0);
  const waiting = gate.drain(1000);
  assert.equal(await expired, false);
  release();
  assert.equal(await waiting, true);
});

test('idle drain succeeds and invalid bounds leave admission open', async () => {
  const gate = createDispatchAdmission();
  for (const invalid of [-1, NaN, Infinity, 0.5, '1']) {
    await assert.rejects(gate.drain(invalid), RangeError);
    assert.equal(gate.closed, false);
  }
  assert.equal(await gate.drain(0), true);
  assert.equal(gate.closed, true);
});
