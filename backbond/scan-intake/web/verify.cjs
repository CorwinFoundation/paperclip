'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2] || __dirname + '/scan.js', 'utf8');

async function page(sequence) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', checked: false, hidden: false, disabled: false,
      textContent: '', handlers: {}, type: 'text',
      addEventListener(event, handler) { this.handlers[event] = handler; },
      querySelectorAll() { return []; }, focus() {},
    });
    return elements.get(id);
  }
  element('transport').value = 'rest';
  element('auth-type').value = 'none';
  element('endpoint').value = 'https://example.test/agent';
  element('email').value = 'test@example.test';
  element('agent-label').value = 'Test agent';
  element('consent-owner').checked = true;
  const timers = new Map();
  let nextTimer = 0;
  let polls = 0;
  const context = {
    document: {getElementById: element, querySelectorAll: () => [], querySelector: () => ({})},
    URL, AbortController, Date,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url) {
      let body;
      if (url.endsWith('/config')) body = {scan_start_enabled: true, terms_version: 'test'};
      else if (url.endsWith('/connection-checks')) body = {ok: true, connection_proof: 'proof', expires_in: 900};
      else if (url.endsWith('/scans')) body = {status: 'running', status_token: 'synthetic-status-token'};
      else if (url.endsWith('/scan-status')) {
        polls++;
        const next = sequence.shift();
        if (next instanceof Error) throw next;
        assert.ok(next, 'unexpected extra status request');
        body = next;
      } else throw Error('Unexpected request: ' + url);
      return {ok: true, json: async () => body};
    },
  };
  vm.runInNewContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  await element('test-connection').handlers.click();
  await element('scan-form').handlers.submit({preventDefault() {}});
  return {
    text: () => element('status').textContent,
    element,
    pollCount: () => polls,
    pending: () => [...timers.values()].filter(t => t.delay === 10000).length,
    async tick() {
      const entry = [...timers.entries()].find(([, timer]) => timer.delay === 10000);
      assert.ok(entry, 'expected next status poll');
      timers.delete(entry[0]);
      await entry[1].fn();
    },
  };
}

(async () => {
  let scopeBoundaryChecks = 0;
  for (const [scope, expected] of [
    ['starter-100@3', '100 cases; starter-100@3'],
    ['starter-voice-50@1', '50 cases; starter-voice-50@1'],
    ['starter-voice-50@0', '50 cases; version unavailable'],
    ['starter-voice-0@1', '50 cases; version unavailable'],
    ['starter--50@1', '50 cases; version unavailable'],
    ['starter-voice-50@1<script>', '50 cases; version unavailable'],
    [42, '50 cases; version unavailable'],
  ]) {
    const result = await page([{status: 'queued', accepted_scope_version: scope,
      accepted_pack_total: scope === 'starter-100@3' ? 100 : 50,
      queue_acknowledged: true, cancel_available: true}]);
    assert.equal(result.element('accepted-scope').textContent, expected);
    assert.equal(result.element('cancel-scan').hidden, false);
    scopeBoundaryChecks++;
  }
  if (process.env.STARTER_SCOPE_BOUNDARIES_ONLY === '1') {
    console.log(`PASS: ${scopeBoundaryChecks} scope boundary regressions; no real requests.`);
    return;
  }
  const populated={status:'queued',request_reference:'BB-0123456789abcdef0123',accepted_pack_total:100,accepted_scope_version:'starter-100@4',last_confirmed_progress_at:'2026-10-20T10:00:00+00:00',heartbeat_at:'2026-10-20T10:05:00Z',queue_acknowledged:true,cancel_available:true};
  const metadata=await page([populated,{...populated,queue_acknowledged:false}, {status:'queued',request_reference:'internal-secret',accepted_scope_version:'<script>',last_confirmed_progress_at:123,cancel_available:true}]);
  assert.equal(metadata.element('case-reference').textContent,populated.request_reference);
  assert.equal(metadata.element('accepted-scope').textContent,'100 cases; starter-100@4');
  assert.equal(metadata.element('confirmed-progress').textContent,'2026-10-20T10:00:00.000Z');
  assert.equal(metadata.element('heartbeat-time').textContent,'2026-10-20T10:05:00.000Z');
  assert.match(metadata.text(),/is queued.*can cancel/);assert.equal(metadata.element('cancel-scan').hidden,false);
  await metadata.tick();assert.match(metadata.text(),/confirmation pending/);assert.equal(metadata.element('cancel-scan').hidden,true);
  await metadata.tick();for(const id of ['case-reference','accepted-scope','confirmed-progress'])assert.equal(metadata.element(id).textContent,'Unavailable');
  assert.equal(metadata.element('cancel-scan').hidden,true);
  const progress = count => ({status: 'running', processed_count: count, accepted_pack_total: 100,
    usable_count: 0, unavailable_count: count});
  const stopped = {status: 'incomplete', phase: 'report_preparation', processed_count: 3,
    accepted_pack_total: 100, usable_count: 0, unavailable_count: 3, report_emailed: false};
  const regression = await page([progress(1), progress(2), stopped]);
  assert.match(regression.text(), /1 of 100/);
  await regression.tick();
  assert.match(regression.text(), /2 of 100/);
  await regression.tick();
  assert.match(regression.text(), /stopped before completion/i);
  assert.match(regression.text(), /3 of 100 cases processed/);
  assert.match(regression.text(), /0 usable; 3 unavailable/);
  assert.doesNotMatch(regression.text(), /is running|counts are not available|allowance has been restored/i);
  assert.equal(regression.pending(), 1);
  for (const status of ['incomplete', 'incomplete_cost_cap', 'failed']) {
    const terminal = await page([{...stopped, status}]);
    assert.match(terminal.text(), /stopped before completion/i);
    assert.match(terminal.text(), /Report delivery status is unconfirmed/);
    assert.equal(terminal.pending(), 1);
  }
  const emailed = await page([{...stopped, report_emailed: true}]);
  assert.match(emailed.text(), /Report delivery status is unconfirmed/);
  const noCounts = await page([{status: 'incomplete'}]);
  assert.doesNotMatch(noCounts.text(), /0 of 100|undefined|null|unknown usable/);
  const noTotal = await page([{status: 'incomplete', processed_count: 3}]);
  assert.match(noTotal.text(), /3 cases processed/);
  assert.doesNotMatch(noTotal.text(), /of 100/);
  const unknown = await page([{status: 'unexpected-state'}]);
  assert.match(unknown.text(), /not confirmed/);
  assert.doesNotMatch(unknown.text(), /is running/);
  const queued = await page([{status: 'queued'}]);
  assert.match(queued.text(), /Request confirmation pending/);
  assert.doesNotMatch(queued.text(), /is queued|can cancel/);
  assert.equal(queued.pending(), 1);
  const running = await page([{status: 'running'}]);
  assert.match(running.text(), /running.*counts are not available/i);
  assert.equal(running.pending(), 1);
  const pendingMail = await page([{status: 'finished', report_emailed: false}]);
  assert.match(pendingMail.text(), /Report status is unconfirmed/);
  assert.equal(pendingMail.pending(), 1);
  const complete = await page([{status: 'finished', report_emailed: true}]);
  assert.match(complete.text(), /Scan processing finished/);
  assert.equal(complete.pending(), 1);
  const dispatchUnknown = await page([{status: 'dispatch_unknown'}]);
  assert.match(dispatchUnknown.text(), /outcome is unknown/);
  assert.doesNotMatch(dispatchUnknown.text(), /is running/);
  assert.equal(dispatchUnknown.pending(), 1);
  for (const status of ['cancelled', 'interrupted', 'launch_failed', 'connection_failed']) {
    const terminal = await page([{status}]);
    assert.match(terminal.text(), /Scan status:/);
    assert.equal(terminal.pending(), 1);
  }
  const network = await page([Error('synthetic network failure'), progress(2)]);
  assert.match(network.text(), /Latest status update unavailable/);
  assert.equal(network.pending(), 1);
  await network.tick();
  assert.match(network.text(), /2 of 100/);
  const otherScope = await page([{status: 'running', processed_count: 2}]);
  assert.match(otherScope.text(), /2 cases processed/);
  assert.doesNotMatch(otherScope.text(), /of 100|cases completed/);
  for (const status of ['complete', 'completed']) {
    const result = await page([{status, report_emailed: true}]);
    assert.match(result.text(), /Scan processing finished/);
    assert.doesNotMatch(result.text(), /inbox|Scan completed|accepted the report email/);
  }
  const retained = await page([stopped, Error('synthetic disconnect'), progress(1)]);
  await retained.tick();
  assert.match(retained.text(), /stopped before completion/);
  assert.match(retained.text(), /3 of 100 cases processed/);
  assert.match(retained.text(), /Latest status update unavailable/);
  await retained.tick();
  assert.match(retained.text(), /stopped before completion/);
  assert.doesNotMatch(retained.text(), /1 of 100|is running/);
  const finishedDisconnect = await page([{status:'complete'}, Error('synthetic disconnect')]);
  await finishedDisconnect.tick();
  assert.match(finishedDisconnect.text(), /Scan processing finished/);
  assert.match(finishedDisconnect.text(), /Latest status update unavailable/);
  const lifecycle = {version:'starter_lifecycle@1', intake_status:'recorded',
    report_status:'drafting', delivery_status:'not_ready', updated_at:null};
  const stages = {
    intake_status: {pending:/acknowledgement is pending/,recorded:/Intake recorded/,failed:/acknowledgement failed/,unknown:/Intake status is unconfirmed/,unavailable:/Intake status is unconfirmed/},
    report_status: {awaiting_results:/awaiting scan results/,drafting:/report is in preparation/,review:/under BackBond review/,changes_requested:/report is in preparation/,awaiting_ceo_approval:/under BackBond review/,approved:/review is complete/,unavailable:/Report status is unconfirmed/},
    delivery_status: {not_ready:/delivery is not ready/,pending:/delivery is pending/,sending:/delivery is in progress/,provider_accepted:/Report prepared for BackBond review/,failed:/Report delivery failed/,unknown:/Report delivery status is unconfirmed/,unavailable:/Report delivery status is unconfirmed/},
  };
  let lifecycleChecks=0;
  for(const [field, values] of Object.entries(stages)){
    for(const [value, expected] of Object.entries(values)){
      const result=await page([{status:'complete',lifecycle:{...lifecycle,[field]:value}}]);
      assert.match(result.text(),expected);
      assert.doesNotMatch(result.text(),/CEO|info@|inbox|customer.*email/i);
      assert.equal(result.pending(),1);
      lifecycleChecks++;
    }
    for(const value of [undefined,'future_state','toString']){
      const result=await page([{status:'complete',lifecycle:{...lifecycle,[field]:value}}]);
      assert.match(result.text(),new RegExp((field==='intake_status'?'Intake':field==='report_status'?'Report':'Report delivery')+' status is unconfirmed'));
      lifecycleChecks++;
    }
  }
  for(const value of [undefined,{}, {...lifecycle,version:'starter_lifecycle@2'}]){
    const result=await page([{status:'complete',lifecycle:value}]);
    assert.match(result.text(),/Intake status is unconfirmed/);
    assert.match(result.text(),/Report status is unconfirmed/);
    assert.match(result.text(),/Report delivery status is unconfirmed/);
    lifecycleChecks++;
  }
  const updates=await page([{...stopped,lifecycle},
    {status:'running',lifecycle:{...lifecycle,report_status:'review'}},
    {...stopped,lifecycle:{...lifecycle,report_status:'approved',delivery_status:'provider_accepted'}}]);
  await updates.tick();
  assert.match(updates.text(),/stopped before completion/);
  assert.match(updates.text(),/under BackBond review/);
  await updates.tick();
  assert.match(updates.text(),/Report prepared for BackBond review/);
  assert.match(updates.text(),/3 of 100 cases processed/);
  lifecycleChecks++;
  // Optional real backend fixture: feed a saved public scan-status response,
  // generated by the backend's restrictive fixture, through the actual page.
  // This hook is not evidence of a round trip until such a fixture is supplied.
  if(process.argv[3]){
    const fixture=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
    assert.equal(fixture.response.lifecycle.version,'starter_lifecycle@1');
    assert.ok(fixture.expected_text.length);
    const result=await page([fixture.response]);
    for(const expected of fixture.expected_text)assert.ok(result.text().includes(expected),expected);
    for(const [id,text] of Object.entries(fixture.expected_metadata||{}))assert.equal(result.element(id).textContent,text,id);
    if(Object.hasOwn(fixture,'cancel_hidden'))assert.equal(result.element('cancel-scan').hidden,fixture.cancel_hidden);
    console.log('PASS: supplied backend response rendered by actual page script.');
  }
  console.log(`PASS: 23 execution scenarios, metadata/capability assertions and ${lifecycleChecks} lifecycle checks; no real requests.`);

})().catch(error => { console.error(error); process.exitCode = 1; });
