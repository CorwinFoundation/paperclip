'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/pilot.js', 'utf8');
function harness({ack = {success:true}, hang = false, readyEvent = 'CONTENT_LOADED'} = {}) {
  const calls = [];
  const window = {};
  const context = {window, setTimeout, clearTimeout, QuickSightEmbedding: {
    async createEmbeddingContext() {
      return {async embedQuickChat(frame, content) {
        calls.push({frame, content});
        if (!hang) content.onMessage({eventName:readyEvent});
        return {async sendPrompt(prompt) {calls.push(prompt); return ack;}};
      }};
    }
  }};
  vm.runInNewContext(source, context);
  return {api:window.backbondQuickPilot, calls};
}
(async () => {
  // Python passes its actual AsyncChat.open parameters through this mode.
  // Exercise the deployed JavaScript, rather than a mocked mount response.
  if (process.argv[2] === '--mount-contract') {
    const parameters = JSON.parse(fs.readFileSync(0, 'utf8'));
    const batch = harness({readyEvent:'EXPERIENCE_INITIALIZED'});
    const result = await batch.api.mount(parameters);
    assert.equal(batch.calls.length, 1);
    console.log(JSON.stringify(result));
    return;
  }
  const h = harness();
  assert.equal((await h.api.mount({embedUrl:'https://embed.example',agentId:'agent',timeoutMs:100})).ready, true);
  assert.equal(h.calls[0].content.agentOptions.fixedAgentId, 'agent');
  assert.equal(h.calls[0].content.promptOptions.enablePrivateMode, true);
  assert.equal(h.calls[0].content.promptOptions.showChatHistory, false);
  assert.equal(h.calls[0].frame.framePermissions.clipboardRead, false);
  const exact = 'quote "hi"\nשלום {{prompt}}';
  assert.equal((await h.api.send({prompt:exact,timeoutMs:100})).submitted, true);
  assert.equal(h.calls[1], exact);
  await assert.rejects(h.api.mount({embedUrl:'other',agentId:'other',timeoutMs:100}));
  const bad = harness({ack:{success:false,message:'PRIVATE'}});
  await bad.api.mount({embedUrl:'url',agentId:'agent',timeoutMs:100});
  await assert.rejects(bad.api.send({prompt:'test',timeoutMs:100}), /status unknown/);
  await assert.rejects(bad.api.send({prompt:'retry',timeoutMs:100}), /unavailable/);
  const late = harness({hang:true});
  await assert.rejects(late.api.mount({embedUrl:'url',agentId:'agent',timeoutMs:5}), /mount failed/);
  // Reproduce the live Quick account: channel initialized, no CONTENT_LOADED.
  const initialized = harness({readyEvent:'EXPERIENCE_INITIALIZED'});
  assert.equal((await initialized.api.mount({embedUrl:'url',agentId:'agent',timeoutMs:100})).ready, true);
  assert.equal((await initialized.api.send({prompt:exact,timeoutMs:100})).submitted, true);
  assert.equal(initialized.calls[1], exact);
  const longMount = harness({readyEvent:'EXPERIENCE_INITIALIZED'});
  assert.equal((await longMount.api.mount({embedUrl:'url',agentId:'agent',timeoutMs:60000})).ready, true);
  for (const timeoutMs of [0, -1, 60001, 1.5, NaN, Infinity, null, '60000']) {
    const invalid = harness();
    await assert.rejects(invalid.api.mount({embedUrl:'url',agentId:'agent',timeoutMs}), /mount failed/);
    assert.equal(invalid.calls.length, 0, 'invalid mount deadline must not invoke the SDK');
  }
  for (const timeoutMs of [0, -1, 30001, 60000, 1.5, NaN, Infinity, null, '30000']) {
    const invalidSend = harness();
    await invalidSend.api.mount({embedUrl:'url',agentId:'agent',timeoutMs:60000});
    await assert.rejects(invalidSend.api.send({prompt:'never submit',timeoutMs}), /status unknown/);
    assert.equal(invalidSend.calls.length, 1, 'invalid send deadline must not submit a prompt');
    await assert.rejects(invalidSend.api.send({prompt:'retry',timeoutMs:100}), /unavailable/);
  }
  const maxSend = harness();
  await maxSend.api.mount({embedUrl:'url',agentId:'agent',timeoutMs:60000});
  assert.equal((await maxSend.api.send({prompt:'send remains bounded',timeoutMs:30000})).submitted, true);
  console.log('Quick SDK harness: ready events, deadline boundaries and pre-submit validation passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
