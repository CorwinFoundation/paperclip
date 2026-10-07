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
    article.append(text('p','What we sent'), text('pre', test.prompt));
    article.append(text('p','What the agent replied'));
    const reply = String(test.reply ?? 'No reply received.');
    article.append(text('pre',reply.length > 600 ? reply.slice(0,600) + '...' : reply));
    if (reply.length > 600) { const full = document.createElement('details'); full.append(text('summary','Read the complete reply'),text('pre',reply)); article.append(full); }
    if (test.verdict === 'connection_error') article.append(text('p', test.reason || test.error || 'No usable result was received.'));
    else if (test.reason) article.append(text('p', 'Why it was marked this way: ' + test.reason));
    if (advice) {
      const [why, fix] = guidance[test.family] || ['This response did not meet the test criteria.', 'Review the message and response, adjust the agent instructions or permissions, then rescan.'];
      article.append(text('p', 'Why it matters: ' + why),text('p','Fix hint: ' + fix));
    }
    parent.append(article);
  }
  async function load() {
    const match = location.pathname.match(/^\/scan\/r\/([a-f0-9]{32})\/?$/i);
    if (!match) { $('report-error').textContent = 'This report link is invalid. Open the complete link from your report email.'; return; }
    try {
      const response = await fetch('https://api.backbond.ai/scan/v1/reports/' + match[1], {credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
      if (!response.ok) { $('report-error').textContent = response.status === 404 || response.status === 410 ? 'This report is unavailable or has expired. Reports stay viewable for 180 days.' : 'The report service is unavailable. Try again later.'; return; }
      const data = await response.json();
      if (!Array.isArray(data.tests) || !['starter-100@1','starter-100@2','starter-100@3'].includes(data.pack_version)) throw new Error('schema');
      // could_not_run (starter-100@2 and @3) is its own verdict upstream; here it sits in the not-run bucket, never in fail.
      const tests = data.tests.map(item => ({...item, verdict: ['pass','fail'].includes(item.verdict) ? item.verdict : 'connection_error'}));
      const when = new Date(data.created_at);
      if (!Number.isFinite(when.getTime())) throw new Error('date');
      const date = when.toISOString().replace('T',' ').replace(/\.\d{3}Z$/, ' UTC');
      $('agent-name').textContent = data.agent_label || 'Your agent';
      document.title = (data.agent_label || 'Your agent') + ' - QA scan report | BackBond';
      $('report-meta').textContent = String(data.endpoint_host || '') + ' / ' + date + ' / ' + data.pack_version;
      const notRun = tests.filter(t => t.verdict === 'connection_error');
      $('test-count').textContent = (tests.length - notRun.length) + ' tests ran out of ' + tests.length + ' listed tests.';
      const summary = document.createElement('p'); $('test-count').before(summary);
      if (data.outcome === 'incomplete') {
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
      if (!failed.length) $('failures').append(text('p','No tests with a usable result need attention. Review any tests that could not run.'));
      failed.forEach(t => testDetail(t,$('failures'),true));
      if (notRun.length) { $('connection-section').hidden = false; notRun.forEach(t=>testDetail(t,$('connections'),false)); }
      if (!tests.some(t=>t.verdict==='pass')) $('passes').append(text('p','No passed test results were received.'));
      $('scope').textContent = 'Results of the tests listed above, run on ' + date + ' against this version of your agent. This is not a penetration test. Results apply only to these tests and this agent version. If you change the agent, results can change - rescan after fixes.';
      const partner = /^[a-z0-9-]{1,60}$/.test(data.partner || '') ? data.partner : 'partner';
      $('contact').href = 'mailto:info@backbond.ai?source=' + encodeURIComponent(partner + '-report') + '&subject=Full%20agent%20scan';
      $('report-error').hidden = true; $('report').hidden = false;
    } catch { $('report-error').textContent = 'The report could not be loaded. Try again later.'; }
  }
  load();
})();
