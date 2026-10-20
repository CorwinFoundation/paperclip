import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createDispatchAdmission, createDispatchControl } from '../server/src/services/dispatch-admission.ts';
import { dispatchRequester, replaceAfterDrain } from './dispatch-supervisor.mjs';

test('real HTTP transport and admission preserve claims while denying replacement; rollback resumes', async () => {
  const admission = createDispatchAdmission();
  let running = 1;
  const control = createDispatchControl(admission, async () => running);
  const server = http.createServer(async (req, res) => {
    if (req.headers.authorization !== 'Bearer fixture') { res.writeHead(403).end(); return; }
    try {
      const action = req.url.split('/').at(-1);
      const value = action === 'drain' ? await control.drain(0) : action === 'resume' ? control.resume() : await control.status();
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(value));
    } catch { res.writeHead(500).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const request = dispatchRequester(origin, 'Bearer fixture');
    const release = admission.acquire();
    let replaced = false;
    await assert.rejects(replaceAfterDrain({ request, replace: async () => { replaced = true; } }), /not restart-ready/);
    assert.equal(replaced, false);
    assert.equal(admission.active, 1);
    assert.equal(admission.closed, false);
    release(); running = 0;
    await assert.rejects(replaceAfterDrain({ request, replace: async () => {
      assert.equal(admission.closed, true);
      assert.equal(admission.acquire(), null);
      throw Error('fixture replacement failure');
    } }), /fixture replacement failure/);
    assert.equal(admission.closed, false);
    const nextClaim = admission.acquire();
    assert.equal(typeof nextClaim, 'function'); nextClaim();
    await replaceAfterDrain({ request, replace: async () => { replaced = true; assert.equal(admission.closed, true); } });
    assert.equal(replaced, true);
    assert.equal(admission.active, 0);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
