import test from 'node:test';
import assert from 'node:assert/strict';
import { dispatchRequester, replaceAfterDrain } from './dispatch-supervisor.mjs';
const idle = { closed: true, claims: 0, running: 0, restartReady: true };

test('replacement runs only after drain and fresh authoritative status', async () => {
  const calls = [];
  await replaceAfterDrain({ request: async action => { calls.push(action); return idle; }, replace: async () => calls.push('replace') });
  assert.deepEqual(calls, ['drain', 'status', 'replace']);
});

for (const [name, value] of Object.entries({ active: { ...idle, running: 1 }, claims: { ...idle, claims: 1 }, open: { ...idle, closed: false }, unknown: {}, stale: { ...idle, restartReady: false } })) {
  test(`${name} admission prevents replacement and resumes queued dispatch`, async () => {
    const calls = [];
    await assert.rejects(replaceAfterDrain({ request: async action => { calls.push(action); return value; }, replace: async () => calls.push('replace') }));
    assert.deepEqual(calls, ['drain', 'resume']);
  });
}

test('concurrent resume between drain and status prevents replacement', async () => {
  const calls = [];
  await assert.rejects(replaceAfterDrain({ request: async action => { calls.push(action); return action === 'drain' ? idle : { ...idle, closed: false }; }, replace: async () => calls.push('replace') }));
  assert.deepEqual(calls, ['drain', 'status', 'resume']);
});

test('failed replacement resumes old process; failed resume names recovery', async () => {
  for (const resumeFails of [false, true]) {
    const calls = [];
    await assert.rejects(replaceAfterDrain({ request: async action => { calls.push(action); if (resumeFails && action === 'resume') throw Error('offline'); return idle; }, replace: async () => { calls.push('replace'); throw Error('replacement failed'); } }), resumeFails ? /Operator must resume explicitly/ : /replacement failed/);
    assert.deepEqual(calls, ['drain', 'status', 'replace', 'resume']);
  }
});

test('uninstrumented first load refuses replacement; request uses admin auth and bounded transport', async () => {
  const calls = [];
  const request = dispatchRequester('http://127.0.0.1:3100', 'Bearer fixture', async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response('', { status: 404 });
  });
  await assert.rejects(replaceAfterDrain({ request, replace: async () => assert.fail('must not replace') }), /Operator must resume explicitly/);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.headers.Authorization, 'Bearer fixture');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.signal instanceof AbortSignal, true);
  assert.deepEqual(JSON.parse(calls[0].options.body), { timeoutMs: 30000 });
  assert.match(calls[1].url, /\/resume$/);
});

test('credentials cannot be forwarded through redirects or credential-bearing URLs', () => {
  for (const origin of ['file:///tmp/a', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com/?q=x']) assert.throws(() => dispatchRequester(origin, 'Bearer fixture'));
  assert.throws(() => dispatchRequester('http://localhost:3100', ''));
});
