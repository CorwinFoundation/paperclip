'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const guidance = {
    prompt_injection: ['Untrusted messages can redirect an agent away from its instructions.', 'Separate untrusted input from instructions and restrict tool permissions.'],
    system_prompt: ['Internal instructions may reveal details users should not receive.', 'Keep sensitive information out of instructions and check responses for disclosure.'],
    secrets_pii: ['Replies may expose private information.', 'Remove secrets from accessible context and enforce access controls before retrieval.'],
    discrimination: ['People may receive unequal treatment.', 'Review the affected decisions and test equivalent requests across groups.'],
    hallucination: ['Unsupported facts can mislead users.', 'Require sources for factual claims and allow the agent to state uncertainty.'],
    out_of_scope: ['The agent may attempt actions beyond its authority.', 'Limit tools and require confirmation before consequential actions.'],
    harmful_content: ['The agent may provide instructions that cause harm.', 'Set explicit response boundaries and route sensitive requests to a person.'],
    system_prompt_leak: ['Internal instructions may reveal details users should not receive.', 'Keep sensitive information out of instructions and check responses for disclosure.'],
    secrets_pii_leak: ['Replies may expose private information.', 'Remove secrets from accessible context and enforce access controls before retrieval.'],
    made_up_facts: ['Unsupported facts can mislead users.', 'Require sources for factual claims and allow the agent to state uncertainty.'],
    out_of_scope_actions: ['The agent may attempt actions beyond its authority.', 'Limit tools and require confirmation before consequential actions.'],
    jailbreak_roleplay: ['Role-play or bypass requests may defeat instructions.', 'Test instruction priority and enforce action limits outside the model.'],
    unsafe_output: ['Output an app runs or renders (scripts, shell commands, SQL) can cause harm downstream.', 'Escape or validate agent output before any system executes or renders it.'],
    jailbreak: ['Role-play or bypass requests may defeat instructions.', 'Test instruction priority and enforce action limits outside the model.'],
    answers_normal_requests: ['An agent that refuses ordinary requests fails the people it is meant to help.', 'Narrow refusal rules to genuinely risky requests and check that everyday questions get a direct answer.'],
    refusal_escalation: ['The agent may fail to stop or ask for help when needed.', 'Define refusal and escalation conditions and test the handoff.']
  };
  const text = (tag, value) => { const node = document.createElement(tag); node.textContent = String(value ?? ''); return node; };
  const names = {p1_1_factuality_golden:'Factual accuracy',p1_3_consistency_golden:'Answer consistency',p1_4_refusal_golden:'Refusal boundaries',p1_5_calibration_golden:'Uncertainty and calibration',p1_6_eval_coverage_breadth_golden:'Evaluation breadth',p2_5_sentiment_skew_golden:'Sentiment bias',p3_2_indirect_injection_floor30:'Indirect prompt injection',p3_3_secret_exfil_floor30:'Secret disclosure',p3_5_multiturn_jailbreak:'Jailbreak attempts',p3_6_output_attack:'Output attacks',prompt_injection:'Prompt injection',system_prompt_leak:'System prompt leak',secrets_pii_leak:'Secrets and personal data',discrimination:'Discrimination',made_up_facts:'Made-up facts',out_of_scope_actions:'Out-of-scope actions',harmful_content:'Harmful content',jailbreak_roleplay:'Jailbreak by role-play',refusal_escalation:'Pressure after a refusal',unsafe_output:'Unsafe output',answers_normal_requests:'Answers normal requests'};
  const label = family => names[family] || String(family || 'Other').replaceAll('_', ' ');
  function testDetail(test, parent, advice) {
    const article = document.createElement('article');
    article.append(text('h3', label(test.family) + ' / ' + (test.id || 'Test')));
    const prompt = typeof test.prompt === 'string' ? test.prompt : '';
    const reply = typeof test.reply === 'string' ? test.reply : '';
    if (!prompt.trim() && !reply.trim()) {
      article.append(text('p', test.evidenceOmitted
        ? 'This test pack does not include request or reply text in its report. The recorded verdict and explanation are shown below.'
        : 'Request and reply text are unavailable for this test. The recorded result is shown below.'));
    } else {
      article.append(text('p','What we sent'), text('pre', prompt.trim() ? prompt : 'Request text is unavailable.'));
      article.append(text('p','What the agent replied'));
      article.append(text('pre',reply.trim() ? (reply.length > 600 ? reply.slice(0,600) + '...' : reply) : 'No reply text was recorded.'));
      if (reply.length > 600) { const full = document.createElement('details'); full.append(text('summary','Read the complete reply'),text('pre',reply)); article.append(full); }
    }
    if (test.verdict === 'connection_error') article.append(text('p', test.reason || test.error || 'No usable result was received.'));
    else if (test.reason) article.append(text('p', 'Why it was marked this way: ' + test.reason));
    if (advice) {
      const [why, fix] = guidance[test.family] || ['This response did not meet the test criteria.', 'Review the message and response, adjust the agent instructions or permissions, then rescan.'];
      article.append(text('p', 'Why it matters: ' + why),text('p','Fix hint: ' + fix));
    }
    parent.append(article);
  }
  // BEAAA-38932: board-approved copy from BEAAA-38928 messaging-draft v0.2 (sections 1, 2, 4). starter-100@3 only.
  const STARTER_TESTED = ['This was a 100-message Starter scan (pack starter-100@3). We sent your agent ten single messages in each of ten areas and graded every reply with a fixed rubric:', 'prompt injection, system prompt leak, secrets and personal data leak, discrimination and bias, made-up facts, out-of-scope actions, harmful content, jailbreak by role-play, refusal under pressure, and answering normal requests.', 'The last area checks the other side: ten ordinary requests (a refund question with the policy pasted in, a seat-price calculation, an email rewrite). An agent that refuses everything fails it.', 'Your Starter score describes these 100 messages only. It is not the Full Scan score and it is not used for underwriting.'];
  const STARTER_OMITTED = ['Conversations. Every Starter test is one message and one reply. Attacks that build over several turns were not tried.', 'Actions. We read what your agent said, not what it did. Refunds, account changes, emails and other tool calls were not run against a sandbox.', 'Repeat behaviour. Each test ran once, so the Starter cannot tell you how often a failure happens.', 'Your own policy. The rubrics are generic. Your refund rules, escalation rules and product limits were not checked.', 'Voice, other languages, documents, memory and connected tools (MCP) were out of scope.'];
  const STARTER_NOT_RUN = 'Anything we did not run is marked N/A or not run. Neither one counts as a pass.';
  // Until /scan/full/new exists the button opens the current contact route.
  const FULL_SCAN_SCOPE_URL = partner => 'mailto:info@backbond.ai?source=' + encodeURIComponent(partner + '-report-full-scan-scope') + '&subject=Full%20Scan%20scope';
  const API = 'https://api.backbond.ai/scan/v1/reports/';
  const request = (url, options = {}) => fetch(url, {...options, credentials:'include', cache:'no-store', referrerPolicy:'no-referrer'});
  const combinedPacks = data => {
    const base = {pack_id:'starter-100', pack_version:data.pack_version, status:'complete', tests:data.tests || []};
    const supplied = Array.isArray(data.result_sets) ? data.result_sets : [base];
    const expected = [
      {pack_id:'starter-100', pack_version:'starter-100@3'},
      {pack_id:'starter-voice-50', pack_version:'starter-voice-50@1'},
      {pack_id:'codex-targeted-20', pack_version:'codex-targeted-20@1'}
    ];
    return expected.map(want => supplied.find(set => set.pack_id === want.pack_id) || {...want,status:'not_run',tests:[]});
  };
  const uniqueCombinedTests = packs => {
    const seen = new Set();
    return packs.flatMap(pack => (pack.tests || []).map(item => ({...item,pack_id:pack.pack_id,pack_version:pack.pack_version})))
      .filter(item => { const id = String(item.id || item.test_id || ''); if (!id || seen.has(id)) return false; seen.add(id); return true; });
  };
  const validateVoicePack = data => {
    const layout = [
      ['VDN',10,'in_domain_normal'], ['VPF',5,'product_fabrication'],
      ['VASR',5,'asr_shaped_input'], ['VML',15,'multilingual_guardrails'],
      ['VSS',5,'screen_share_readback'], ['VSP',5,'summary_poisoning'],
      ['VGD',5,'goals_data_collection'], ['VHD',5,'human_disclosure'],
      ['VAA',5,'abuse_toward_agent']
    ];
    const expected = new Map(layout.flatMap(([prefix,count,family]) =>
      Array.from({length:count}, (_,i) => [prefix + '-' + String(i+1).padStart(2,'0'),family])));
    const ids = new Set(data.tests.map(item => item.id));
    if (ids.size !== data.tests.length || data.tests.length > 60 ||
        data.tests.some(item => !expected.has(item.id) || expected.get(item.id) !== item.family) ||
        (data.outcome !== 'incomplete' && ids.size !== 60)) throw new Error('voice schema');
  };
  function verification(token) {
    const host = $('report-error'); host.replaceChildren(); host.hidden = false;
    host.append(text('p','Verify that you are the report recipient to continue.'));
    const requestButton = text('button','Email me a code'); requestButton.type = 'button';
    const form = document.createElement('form'); form.hidden = true;
    const input = document.createElement('input'); input.inputMode = 'numeric'; input.autocomplete = 'one-time-code'; input.pattern = '[0-9]{6}'; input.maxLength = 6; input.required = true; input.setAttribute('aria-label','Six-digit verification code');
    const submit = text('button','Open report'); submit.type = 'submit';
    const status = text('p',''); form.append(input, submit); host.append(requestButton, form, status);
    requestButton.addEventListener('click', async () => {
      requestButton.disabled = true;
      try {
        await request(API + token + '/verification', {method:'POST'});
        status.textContent = 'If this report is available, a code was sent to its original recipient.';
        form.hidden = false; input.focus();
      } catch { status.textContent = 'Verification is temporarily unavailable. Try again later.'; }
      finally { requestButton.disabled = false; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        const response = await request(API + token + '/session', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code:input.value})});
        if (!response.ok) { status.textContent = 'That code is invalid or expired.'; return; }
        await load();
      } catch { status.textContent = 'Verification is temporarily unavailable. Try again later.'; }
      finally { submit.disabled = false; }
    });
  }
  function logout(token) {
    const button = text('button','Sign out'); button.type = 'button';
    button.addEventListener('click', async () => { await request(API + token + '/session', {method:'DELETE'}); $('report').hidden = true; verification(token); });
    $('report').prepend(button);
  }
  function starterScopeBlocks(table, partner, packVersion = 'starter-100@3') {
    const tested = document.createElement('section'); tested.className = 'starter-tested';
    tested.append(text('h2','What this Starter scan tested'));
    STARTER_TESTED.forEach(line => tested.append(text('p', line.replace('starter-100@3', packVersion))));
    const omitted = document.createElement('section'); omitted.className = 'starter-omissions';
    omitted.append(text('h2','What this scan did not look at'));
    const list = document.createElement('ul'); STARTER_OMITTED.forEach(line => list.append(text('li', line))); omitted.append(list, text('p', STARTER_NOT_RUN));
    const upgrade = document.createElement('section'); upgrade.className = 'starter-upgrade';
    const lead = text('p',''); lead.append(text('strong','See what your agent does, not only what it says.'));
    upgrade.append(lead, text('p','A Full Scan is a separate assessment with its own scope, score and report.'));
    const button = text('a','Review Full Scan scope'); button.className = 'button'; button.href = FULL_SCAN_SCOPE_URL(partner); upgrade.append(button);
    table.after(tested, omitted, upgrade);
  }
  async function load() {
    const match = location.pathname.match(/^\/scan\/r\/([a-f0-9]{32})\/?$/i);
    if (!match) { $('report-error').textContent = 'This report link is invalid. Open the complete link from your report email.'; return; }
    try {
      const response = await request(API + match[1]);
      if (!response.ok) { if (response.status === 404) verification(match[1]); else $('report-error').textContent = 'The report service is unavailable. Try again later.'; return; }
      const data = await response.json();
      if (!Array.isArray(data.tests) || !['starter-100@1','starter-100@2','starter-100@3','starter-100@4','starter-voice-50@1'].includes(data.pack_version)) throw new Error('schema');
      const voice = data.pack_version === 'starter-voice-50@1';
      if (voice) validateVoicePack(data);
      const combined = data.report_kind === 'starter_extensions';
      const packs = combinedPacks(data);
      if (combined) data.tests = uniqueCombinedTests(packs);
      // could_not_run (starter-100@2 and @3) is its own verdict upstream; here it sits in the not-run bucket, never in fail.
      const tests = data.tests.map(item => ({...item, evidenceOmitted: data.pack_version === 'starter-100@4', verdict: ['pass','fail'].includes(item.verdict) ? item.verdict : 'connection_error'}));
      const when = new Date(data.created_at);
      if (!Number.isFinite(when.getTime())) throw new Error('date');
      const date = when.toISOString().replace('T',' ').replace(/\.\d{3}Z$/, ' UTC');
      $('agent-name').textContent = data.agent_label || 'Your agent';
      if (combined) $('agent-name').textContent += ' / Starter scan + extensions';
      document.title = (data.agent_label || 'Your agent') + ' - QA scan report | BackBond';
      $('report-meta').textContent = String(data.endpoint_host || '') + ' / ' + date + ' / ' + data.pack_version;
      const notRun = tests.filter(t => t.verdict === 'connection_error');
      $('test-count').textContent = (tests.length - notRun.length) + ' tests ran out of ' + tests.length + ' listed tests.';
      const summary = document.createElement('p'); $('test-count').before(summary);
      if (combined) {
        summary.textContent = 'Starter scan + extensions. Scoring is deferred; no Full Scan score or BackBond Score is published.';
      } else if (voice) {
        summary.textContent = data.outcome === 'incomplete'
          ? 'This scan did not finish: ' + tests.length + ' of 60 tests have a result. No score is published for this pack.'
          : '60 voice-related text/browser tests. Native audio, live screen sharing, spoken output, turn-taking and interruptions were not tested. No score is published for this pack.';
      } else if (data.outcome === 'incomplete') {
        summary.textContent = 'This scan did not finish: ' + tests.length + ' of 100 tests have a result. Starter score: N/A. 10 areas, 100 tests. Not the full BackBond Score.';
      } else if (typeof data.starter_score === 'number') {
        summary.textContent = 'Starter score: ' + Math.round(data.starter_score) + '. 10 areas, 100 tests. Not the full BackBond Score.';
      } else { summary.remove(); }
      $('connection-count').textContent = notRun.length + ' tests could not run or returned no usable result. They are not counted as passes.';
      const families = new Map();
      tests.forEach(t => { const key = String(t.family || 'Other'); if (!families.has(key)) families.set(key, []); families.get(key).push(t); });
      for (const [family, items] of families) {
        const row = document.createElement('tr'); row.append(text('th',label(family))); row.firstChild.scope = 'row';
        [items.filter(t=>t.verdict!=='connection_error').length,items.filter(t=>t.verdict==='pass').length,items.filter(t=>t.verdict==='fail').length,items.filter(t=>t.verdict==='connection_error').length].forEach(value=>row.append(text('td',value)));
        $('families').append(row);
        const passed = items.filter(t => t.verdict === 'pass');
        if (passed.length) { const group = document.createElement('details'); group.append(text('summary',label(family) + ' - ' + passed.length + ' passed')); passed.forEach(t=>testDetail(t,group,false)); $('passes').append(group); }
      }
      const failed = tests.filter(t => t.verdict === 'fail');
      if (combined) {
        const lead = failed.findIndex(t => (t.id || t.test_id) === 'PI-29');
        if (lead > 0) failed.unshift(failed.splice(lead, 1)[0]);
        const table = document.createElement('table'); table.className = 'extension-family-table';
        const head = document.createElement('thead'); const header = document.createElement('tr');
        ['Pack','Family','Ran','Passed','Needs attention','Flaky','Could not run'].forEach(value => header.append(text('th',value))); head.append(header); table.append(head);
        const body = document.createElement('tbody');
        packs.forEach(pack => {
          const grouped = new Map(); (pack.tests || []).forEach(item => { const key=String(item.family||'Other'); if(!grouped.has(key)) grouped.set(key,[]); grouped.get(key).push(item); });
          if (!grouped.size) grouped.set('All families', []);
          grouped.forEach((items,family) => {
            const row=document.createElement('tr'); const status=pack.status === 'not_run';
            const counts = status ? ['not run',0,0,0,0] : [items.filter(t=>t.verdict!=='could_not_run').length,items.filter(t=>['pass','passed'].includes(t.verdict)).length,items.filter(t=>['fail','failed'].includes(t.verdict)).length,items.filter(t=>t.verdict==='flaky').length,items.filter(t=>['could_not_run','connection_error'].includes(t.verdict)).length];
            [pack.pack_version,label(family),...counts].forEach(value=>row.append(text('td',value))); body.append(row);
          });
          (pack.derived_families || []).forEach(derived => {
            const row=document.createElement('tr');
            [pack.pack_version,label(derived.family) + ' (derived overlay)',0,derived.passed || 0,derived.failed || 0,0,derived.could_not_run || 0].forEach(value=>row.append(text('td',value)));
            body.append(row);
          });
        }); table.append(body); $('failures').before(table);
      }
      if (!failed.length) $('failures').append(text('p','No tests with a usable result need attention. Review any tests that could not run.'));
      failed.forEach(t => testDetail(t,$('failures'),true));
      if (notRun.length) { $('connection-section').hidden = false; notRun.forEach(t=>testDetail(t,$('connections'),false)); }
      if (!tests.some(t=>t.verdict==='pass')) $('passes').append(text('p','No passed test results were received.'));
      $('scope').textContent = 'Results of the tests listed above, run on ' + date + ' against this version of your agent. This is not a penetration test. Results apply only to these tests and this agent version. If you change the agent, results can change - rescan after fixes.';
      const partner = /^[a-z0-9-]{1,60}$/.test(data.partner || '') ? data.partner : 'partner';
      if (['starter-100@3','starter-100@4'].includes(data.pack_version)) starterScopeBlocks($('families').closest('table') || $('families'), partner, data.pack_version);
      $('contact').href = 'mailto:info@backbond.ai?source=' + encodeURIComponent(partner + '-report') + '&subject=Full%20agent%20scan';
      $('report-error').replaceChildren(); $('report-error').hidden = true; $('report').hidden = false; logout(match[1]);
    } catch { $('report-error').textContent = 'The report could not be loaded. Try again later.'; }
  }
  load();
})();
