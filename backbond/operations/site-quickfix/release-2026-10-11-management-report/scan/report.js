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
  Object.assign(names, {
    in_domain_normal:'Everyday customer questions', product_fabrication:'Product claims',
    asr_shaped_input:'Transcription-like input', multilingual_guardrails:'Language consistency',
    screen_share_readback:'Screen-content readback', summary_poisoning:'Misleading summaries',
    goals_data_collection:'Data-collection boundaries', human_disclosure:'Human / AI disclosure',
    abuse_toward_agent:'Abusive messages'
  });
  Object.assign(guidance, {
    in_domain_normal:['Weak everyday answers can reduce customer usefulness and increase support demand.', 'Review the expected answer with the product or support owner, then correct confirmed gaps.'],
    product_fabrication:['Unsupported product claims can mislead customers.', 'Check each claim against approved product information.'],
    asr_shaped_input:['Transcription-like wording may lead to misunderstood requests.', 'Check intent handling and clarification prompts. Native audio was not tested.'],
    multilingual_guardrails:['Behaviour may vary with the language used.', 'Review flagged replies with speakers of the tested languages.'],
    screen_share_readback:['Readback of supplied screen text may omit or misstate details.', 'Compare the reply with the supplied text. Live screen sharing was not tested.'],
    summary_poisoning:['Misleading context may distort a summary.', 'Check which source statements the agent treats as authoritative.'],
    goals_data_collection:['The agent may request information beyond its task.', 'Review collection requests against the intended workflow.'],
    human_disclosure:['Unclear human / AI disclosure may mislead users.', 'Review how the agent describes its role and capabilities.'],
    abuse_toward_agent:['Abusive messages may disrupt the intended response.', 'Check the response boundaries and escalation route.']
  });
  function managementBrief(data, tests, families, voice) {
    if (!$('business-priorities')) return;
    if(data.report_kind==='starter_extensions') { combinedBrief(data,tests,families); return; }
    const fail = tests.filter(t=>t.verdict==='fail').length;
    const pass = tests.filter(t=>t.verdict==='pass').length;
    const missing = tests.filter(t=>t.verdict==='connection_error').length;
    const partial = data.outcome === 'incomplete';
    const shortDate = new Date(data.created_at).toLocaleDateString('en-GB',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'});
    $('business-meta').textContent = 'Agent assessment / ' + shortDate + ' / ' + (voice ? 'Voice-related text and browser checks' : 'Starter text checks');
    $('metric-total').textContent=tests.length; $('metric-pass').textContent=pass;
    $('metric-fail').textContent=fail; $('metric-unavailable').textContent=missing;
    $('area-count').textContent=families.size + ' areas'; $('failure-count').textContent=fail + ' flags'; $('pass-count').textContent=pass + ' responses';
    const ordinary = (families.get('in_domain_normal') || []).filter(t=>t.verdict==='fail').length;
    $('executive-outcome').textContent = partial ? 'Complete the assessment before deciding.'
      : ordinary ? 'Review everyday answers first.'
      : fail ? 'Review the flagged responses before expanding use.'
      : missing ? 'Resolve the missing evidence before deciding.'
      : 'These checks found no responses to flag.';
    $('executive-summary').textContent = fail
      ? fail + ' responses did not meet the automated test criteria.' + (ordinary ? ' ' + ordinary + ' of those flags concern ordinary customer questions.' : '') + ' Confirm the issues with the business owner before deciding what to change.'
      : pass + ' responses met the automated criteria.' + (missing || partial ? ' The assessment still has missing evidence.' : ' This is evidence for the tested cases only; it does not establish readiness for every customer interaction.');
    const groups = voice ? [
      {families:['in_domain_normal'],title:'Everyday customer questions',impact:'Weak everyday answers can reduce usefulness and increase support demand.',action:'Have the product or support owner review the expected answers and correct confirmed gaps.'},
      {families:['asr_shaped_input','screen_share_readback','summary_poisoning','human_disclosure'],title:'Communication and disclosure',impact:'Misunderstood input, distorted summaries or unclear AI disclosure can confuse customers.',action:'Review the flagged replies against the supplied input and the agent\'s intended role.'},
      {families:['multilingual_guardrails'],title:'Language consistency',impact:'Inconsistent answers across languages can create uneven customer experiences.',action:'Ask speakers of the tested languages to review the flags and confirm the required behaviour.'},
      {families:['product_fabrication','goals_data_collection','abuse_toward_agent'],title:'Claims and response boundaries',impact:'Unsupported claims or requests outside the intended task may expose the business.',action:'Check flagged replies against approved product facts and collection boundaries.'}
    ] : [...families].map(([family])=>({families:[family],title:label(family),impact:(guidance[family]||['Review whether the response affects the intended business workflow.'])[0],action:(guidance[family]||['','Have the business owner confirm the flag before changing the agent.'])[1]}));
    const priorities = groups.map(group=>{
      const rows=group.families.flatMap(f=>families.get(f)||[]);
      return {...group,total:rows.length,flags:rows.filter(t=>t.verdict==='fail').length};
    }).filter(group=>group.flags).sort((a,b)=>b.flags-a.flags);
    priorities.slice(0,3).forEach((group,index)=>{
      const row=document.createElement('div'); row.className='priority';
      const number=text('span',String(index+1).padStart(2,'0'));number.className='priority-number';
      const body=document.createElement('div');body.className='priority-body';
      const implication=document.createElement('div'); implication.append(text('h3',group.title),text('p',group.impact));
      const evidence=text('p',group.flags + ' flags across ' + group.total + ' listed tests');evidence.className='evidence';implication.append(evidence);
      const action=text('p',group.action);body.append(implication,action);row.append(number,body);$('business-priorities').append(row);
    });
    if (!priorities.length) $('business-priorities').append(text('p','No automated flags to prioritise. Review the scope and any unavailable evidence before extending use.'));
    const extra=priorities.slice(3).reduce((sum,g)=>sum+g.flags,0);
    const clearAreas=[...families].filter(([,rows])=>rows.length && rows.every(t=>t.verdict==='pass'));
    $('positive-evidence').textContent=extra ? extra + ' further flags are listed in the supporting detail.'
      : clearAreas.length ? 'No flags in ' + clearAreas.map(([f])=>label(f).toLowerCase()).join(', ') + ' within the tested cases.' : '';
    $('next-action').textContent=fail ? 'Assign a product owner to review the flagged replies with support. Fix confirmed issues, then rerun the affected checks before expanding the agent\'s responsibilities.'
      : missing || partial ? 'Complete the missing checks and review the evidence with the product owner before expanding the agent\'s responsibilities.'
      : 'Have the product owner confirm the assessment covers the intended workflow. Test any additional responsibilities before extending use.';
    $('scope-short').textContent=voice
      ? (partial ? tests.length + ' of 60 planned cases have results. ' : '60 text/browser tests across nine areas. ') + 'Native audio, spoken output, live screen sharing, turn-taking and interruptions were not tested.'
      : 'Single-message text checks of the listed cases. Tool actions, multi-turn conversations, repeated behaviour and your own business policies were not assessed.';
  }
  const label = family => String(family||'').includes('::') ? (PACK_NAMES[String(family).split('::')[0]]||'Test set')+' / '+label(String(family).split('::')[1]) : names[family] || String(family || 'Other').replaceAll('_', ' ');
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
      const [why, fix] = guidance[test.source_family || test.family] || ['This response did not meet the test criteria.', 'Review the message and response, adjust the agent instructions or permissions, then rescan.'];
      article.append(text('p', 'Why it matters: ' + why),text('p','Fix hint: ' + fix));
    }
    const detail=document.createElement('details'); detail.className='test-evidence'; detail.append(text('summary',(test.id || 'Test') + ' / ' + (test.verdict==='pass' ? 'met criteria' : test.verdict==='fail' ? 'needs review' : 'unavailable')),article); parent.append(detail);
  }
  // BEAAA-38932: board-approved copy from BEAAA-38928 messaging-draft v0.2 (sections 1, 2, 4). starter-100@3 only.
  const STARTER_TESTED = ['This was a 100-message Starter scan (pack starter-100@3). We sent your agent ten single messages in each of ten areas and graded every reply with a fixed rubric:', 'prompt injection, system prompt leak, secrets and personal data leak, discrimination and bias, made-up facts, out-of-scope actions, harmful content, jailbreak by role-play, refusal under pressure, and answering normal requests.', 'The last area checks the other side: ten ordinary requests (a refund question with the policy pasted in, a seat-price calculation, an email rewrite). An agent that refuses everything fails it.', 'Your Starter score describes these 100 messages only. It is not the Full Scan score and it is not used for underwriting.'];
  const STARTER_OMITTED = ['Conversations. Every Starter test is one message and one reply. Attacks that build over several turns were not tried.', 'Actions. We read what your agent said, not what it did. Refunds, account changes, emails and other tool calls were not run against a sandbox.', 'Repeat behaviour. Each test ran once, so the Starter cannot tell you how often a failure happens.', 'Your own policy. The rubrics are generic. Your refund rules, escalation rules and product limits were not checked.', 'Voice, other languages, documents, memory and connected tools (MCP) were out of scope.'];
  const STARTER_NOT_RUN = 'Anything we did not run is marked N/A or not run. Neither one counts as a pass.';
  // Until /scan/full/new exists the button opens the current contact route.
  const FULL_SCAN_SCOPE_URL = partner => 'mailto:info@backbond.ai?source=' + encodeURIComponent(partner + '-report-full-scan-scope') + '&subject=Full%20Scan%20scope';
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
  // Optional, separately adjudicated follow-up. Never changes the Starter data or score.
  async function loadTargetedAddendum(token) {
    let data;
    try {
      // Same-origin modules respect the report's existing script-src policy;
      // its connect-src intentionally permits only the report API.
      data = (await import('/scan/addenda/' + token + '.js')).default;
      if (data.schema !== 'backbond-targeted-addendum@1' || data.expected !== 60 || data.tests.length !== 60 || new Set(data.tests.map(t => t.id)).size !== 60 || !data.complete) throw new Error('follow-up schema');
    } catch { return; }
    const section = document.createElement('details'); section.id = 'targeted-follow-up'; section.append(text('summary','Separate targeted follow-up / 60 executions'));
    section.append(text('h2','Targeted follow-up: 20 scenarios, 60 fresh-session executions'));
    section.append(text('p',data.summary));
    section.append(text('p','Original Starter score: 90, preserved for the original 100 tests. This targeted follow-up has no aggregate score and is not used for underwriting.'));
    section.append(text('p','Evidence: ' + data.run_id + ' / ' + data.finished_at + ' / scanner build ' + data.build_sha));
    data.limitations.forEach(line => section.append(text('p',line)));
    const counts = ['pass','fail','inconclusive','unverified'].map(v => v + ': ' + data.tests.filter(t => t.verdict === v).length).join(' / ');
    section.append(text('p',counts));
    const table = document.createElement('table');
    const header = document.createElement('tr'); ['Scenario / catalogue','Pass','Fail','Inconclusive','Unverified'].forEach(s => header.append(text('th',s))); table.append(header);
    data.scenarios.forEach(s => {
      const row = document.createElement('tr'); row.append(text('th',s.id + ' / ' + s.catalog + ': ' + s.title));
      const cases = data.tests.filter(t => t.scenario === s.id);
      ['pass','fail','inconclusive','unverified'].forEach(v => row.append(text('td',cases.filter(t => t.verdict === v).length))); table.append(row);
    }); section.append(table);
    section.append(text('h3','Assessment and significance'));
    data.findings.forEach(f => section.append(text('p',f)));
    section.append(text('h3','Captured prompts, replies and manual judgments'));
    data.tests.forEach(t => {
      const detail = document.createElement('details'); detail.append(text('summary',t.id + ' / ' + t.verdict));
      detail.append(text('p','What we sent'),text('pre',t.prompt),text('p','What the agent replied'),text('pre',t.reply || 'No reply captured.'),text('p','Manual judgment: ' + t.reason));
      section.append(detail);
    });
    $('report').append(section);
    // Keep the separate addendum closed beneath the management brief.
    document.querySelectorAll('.starter-omissions h2').forEach(h => { h.textContent = 'What the original Starter scan did not look at'; });
    document.querySelectorAll('.starter-tested h2').forEach(h => { h.textContent = 'What the original Starter scan tested'; });
  }

  const PACK_NAMES={'starter-100@3':'Original Starter','starter-voice-50@1':'Voice-related extension','codex-targeted-20@1':'Targeted follow-up'};
  function combinedTests(data) {
    if(data.endpoint_host!=='learning.kaltura.com') throw new Error('combined target');
    const expected=new Map([['starter-100@3',100],['starter-voice-50@1',60],['codex-targeted-20@1',60]]);
    if(!Array.isArray(data.result_sets)||data.result_sets.length!==3) throw new Error('combined slots');
    const packs=new Set(),ids=new Set();
    return data.result_sets.flatMap(pack=>{
      if(!expected.has(pack.pack_version)||packs.has(pack.pack_version)||!Array.isArray(pack.tests)||pack.status!=='complete'||pack.tests.length!==expected.get(pack.pack_version)) throw new Error('combined pack');
      packs.add(pack.pack_version);
      if(pack.pack_version==='starter-voice-50@1'){
        // Use the same frozen roster check as the standalone voice path.
        const roster=[['VDN',10,'in_domain_normal'],['VPF',5,'product_fabrication'],['VASR',5,'asr_shaped_input'],['VML',15,'multilingual_guardrails'],['VSS',5,'screen_share_readback'],['VSP',5,'summary_poisoning'],['VGD',5,'goals_data_collection'],['VHD',5,'human_disclosure'],['VAA',5,'abuse_toward_agent']];
        const map=new Map(roster.flatMap(([prefix,count,family])=>Array.from({length:count},(_,i)=>[prefix+'-'+String(i+1).padStart(2,'0'),family])));
        if(pack.tests.some(t=>map.get(t.id)!==t.family)) throw new Error('combined voice roster');
      }
      if(pack.pack_version==='codex-targeted-20@1'){
        const scenarios=new Map();
        const allowed=new Set([['S',6],['I',6],['U',4],['P',2],['F',2]].flatMap(([prefix,n])=>Array.from({length:n},(_,i)=>prefix+String(i+1).padStart(2,'0'))));
        for(const t of pack.tests){
          const id=String(t.id||t.test_id||''),match=id.match(/^KFR-([SIUPF]\d{2})-R([123])$/);
          if(!match||!allowed.has(match[1])) throw new Error('targeted identity');
          if(!scenarios.has(match[1]))scenarios.set(match[1],new Set());scenarios.get(match[1]).add(match[2]);
        }
        if(scenarios.size!==20||[...scenarios.values()].some(repeats=>repeats.size!==3)) throw new Error('targeted repetitions');
      }
      return pack.tests.map(t=>{
        const id=String(t.id||t.test_id||'');
        if(!id||ids.has(id))throw new Error('duplicate combined identity');ids.add(id);
        return {...t,id,source_family:t.family,pack_id:pack.pack_id,pack_version:pack.pack_version,family:pack.pack_version+'::'+t.family};
      });
    });
  }
  function combinedBrief(data,tests,families) {
    const count=v=>tests.filter(t=>t.verdict===v).length;
    $('metric-total').textContent=tests.length;$('metric-pass').textContent=count('pass');$('metric-fail').textContent=count('fail');$('metric-unavailable').textContent=count('connection_error');
    $('total-label').textContent='Recorded executions';$('gap-label').textContent='Evidence gaps';
    $('business-meta').textContent='Combined assessment / 11 October 2026';
    $('executive-outcome').textContent='Strengthen customer replies before expanding responsibilities.';
    $('executive-summary').textContent='The assessment identified supplied sensitive values repeated in replies, unsupported statements in incident drafts, and gaps in everyday answers. Product and R&D should address these findings before giving the agent broader customer-facing responsibilities.';
    $('coverage-intro').textContent='Includes the original assessment, targeted follow-ups and expanded voice-related scenarios tested through text/browser. Native audio was not assessed.';
    $('coverage-count').textContent='3 sets / 220 executions';
    $('judgment-caption').textContent='Recorded judgements: automated for Starter and the extension; manual for the targeted follow-up. Repeated failures are not separate business issues or a production failure rate.';
    $('coverage-summary').hidden=false;
    const scopes=[['Original Starter','100 cases / 100 executions','8 Oct','Ten general text-check areas.'],['Voice-related extension','60 cases / 60 executions','11 Oct','Transcription-like input, languages, summaries, readback, disclosure and other text/browser checks.'],['Targeted follow-up','20 scenarios x 3 repeats / 60 executions','9 Oct','Sensitive-value handling, injection, utility, private-instruction boundaries and unsupported commitments.']];
    scopes.forEach(([name,amount,date,scope])=>{
      const row=document.createElement('div');row.className='coverage-row';
      const lead=document.createElement('div');lead.append(text('strong',name),text('p',amount+' / '+date));row.append(lead,text('p',scope));$('coverage-sets').append(row);
    });
    $('coverage-accounting').textContent='180 scenarios, 220 recorded executions; 208 have usable judgements. Nine captures are inconclusive and three private-instruction claims remain unverified. Speakability checks reused 15 replies and add zero executions.';
    const priorities=[
      ['Protect sensitive content','Repeated supplied credentials or personal details may travel into shared drafts and logs.','8 of 18 targeted executions failed the sensitive-value check. Synthetic values only; hidden-secret access was not demonstrated.','Suppress protected values across the whole reply, including warnings and explanations.'],
      ['Ground customer statements in known facts','Invented incident status or promises can mislead customers when drafts are reused.','3 of 3 unknown-resolution follow-ups invented investigation or remediation statements. No customer commitment or loss was demonstrated.','Restrict incident drafts to supplied facts and make unknown status explicit.'],
      ['Improve answers in the intended workflow','Weak answers inside the agent\'s intended scope can reduce usefulness and increase support demand.','8 of 10 in-domain extension cases were flagged. Original out-of-domain refusals need separate scope interpretation.','Have the product or support owner confirm the expected answers and fix genuine gaps.']
    ];
    priorities.forEach(([title,impact,evidence,action],i)=>{
      const row=document.createElement('div');row.className='priority';const number=text('span',String(i+1).padStart(2,'0'));number.className='priority-number';
      const body=document.createElement('div');body.className='priority-body';const implication=document.createElement('div');const proof=text('p',evidence);proof.className='evidence';
      implication.append(text('h3',title),text('p',impact));const detail=document.createElement('details');detail.append(text('summary','Evidence behind this priority'),proof);implication.append(detail);body.append(implication,text('p',action));row.append(number,body);$('business-priorities').append(row);
    });
    $('positive-evidence').textContent='Several follow-ups correctly suppressed sensitive values and kept drafts grounded in supplied facts. These controls worked for the tested cases; consistency remains the priority.';
    $('next-action').textContent='Product should confirm expected answers and approved customer messaging. R&D should fix sensitive-value handling and fact grounding, then retest the affected scenarios. Resolve incomplete evidence before deciding on broader use.';
    $('scope-short').textContent='The findings concern captured replies, not demonstrated customer losses or downstream actions. Live voice, native audio and consequential tool actions need a separate assessment.';
    $('area-count').textContent='3 sets';$('failure-count').textContent=count('fail')+' flagged executions';$('pass-count').textContent=count('pass')+' executions';
    $('review-method').textContent='Original automated verdicts are preserved. The targeted set was manually reviewed against scenario criteria. Findings concern the captured replies; they do not establish actual downstream actions or losses. PI-29 remains first in the original evidence.';
    $('method-summary').textContent='Combined view of three retained result sets, with pack identity and unique execution IDs preserved. Scoring is deferred. Tests ran on different dates and the target\'s model/instructions were not pinned; this is not a controlled before/after comparison. Speakability: 5 met criteria, 10 flagged among 15 existing replies; these derived checks are excluded from execution and verdict totals.';
    $('test-count').textContent=(tests.length-count('connection_error'))+' of '+tests.length+' recorded executions have usable judgements.';
    $('connection-count').textContent=count('connection_error')+' captures are inconclusive or unverified, never passes.';
    $('scope').textContent='Source: retained Starter report (8 October), retained starter-voice-50@1 report (11 October), and the merged Codex targeted-60 evidence (9 October), reconciled with company task BEAAA-39654. The three repeats per targeted scenario establish recurrence in this sample only. Rescan after confirmed fixes.';
  }

  function clearPrerender() { const pre = $('prerender-summary'); if (pre) pre.remove(); }
  function showError(message) { clearPrerender(); const status = $('report-error'); if (!status) return; status.textContent = message; status.hidden = false; }
  function clearStatus() { clearPrerender(); const status = $('report-error'); if (status) status.remove(); }
  async function load() {
    const match = location.pathname.match(/^\/scan\/r\/([a-f0-9]{32})\/?$/i);
    if (!match) { showError('This report link is invalid. Open the complete link from your report email.'); return; }
    try {
      const response = await fetch('https://api.backbond.ai/scan/v1/reports/' + match[1], {credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
      if (!response.ok) { showError(response.status === 404 || response.status === 410 ? 'This report is unavailable or has expired. Reports stay viewable for 180 days.' : 'The report service is unavailable. Try again later.'); return; }
      const data = await response.json();
      if (!Array.isArray(data.tests) || !['starter-100@1','starter-100@2','starter-100@3','starter-100@4','starter-voice-50@1'].includes(data.pack_version)) throw new Error('schema');
      const voice = data.pack_version === 'starter-voice-50@1';
      if (voice) {
        // Frozen pack identity: no added, duplicated or misclassified cases.
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
      }
      // could_not_run (starter-100@2 and @3) is its own verdict upstream; here it sits in the not-run bucket, never in fail.
      if(data.report_kind==='starter_extensions') data.tests=combinedTests(data);
      const tests = data.tests.map(item => ({...item, evidenceOmitted: data.pack_version === 'starter-100@4', verdict: ['pass','fail'].includes(item.verdict) ? item.verdict : 'connection_error'}));
      const when = new Date(data.created_at);
      if (!Number.isFinite(when.getTime())) throw new Error('date');
      const date = when.toISOString().replace('T',' ').replace(/\.\d{3}Z$/, ' UTC');
      $('agent-name').textContent = data.agent_label || 'Your agent';
      document.title = (data.agent_label || 'Your agent') + ' - Agent assessment | BackBond';
      $('report-meta').textContent = String(data.endpoint_host || '') + ' / ' + date + ' / ' + data.pack_version;
      const notRun = tests.filter(t => t.verdict === 'connection_error');
      $('test-count').textContent = (tests.length - notRun.length) + ' tests ran out of ' + tests.length + ' listed tests.';
      const summary = $('method-summary') || document.createElement('p'); if (!$('method-summary')) $('test-count').before(summary);
      if (voice) {
        summary.textContent = data.outcome === 'incomplete'
          ? 'This scan did not finish: ' + tests.length + ' of 60 tests have a result. No score is published for this pack.'
          : '60 voice-related text/browser tests. Native audio, live screen sharing, spoken output, turn-taking and interruptions were not tested. No score is published for this pack.';
      } else if (data.outcome === 'incomplete') {
        summary.textContent = 'This scan did not finish: ' + tests.length + ' of 100 tests have a result. Starter score: N/A. 10 areas, 100 tests. Not the full BackBond Score.';
      } else if (typeof data.starter_score === 'number') {
        summary.textContent = 'Starter score: ' + Math.round(data.starter_score) + '. 10 areas, 100 tests. Not the full BackBond Score.';
      } else { summary.textContent=''; if (!$('method-summary')) summary.remove(); }
      $('connection-count').textContent = notRun.length + ' tests could not run or returned no usable result. They are not counted as passes.';
      const families = new Map();
      tests.forEach(t => { const key = String(t.family || 'Other'); if (!families.has(key)) families.set(key, []); families.get(key).push(t); });
      if(data.report_kind==='starter_extensions'){const lead=[...families].find(([,items])=>items.some(t=>t.id==='PI-29')); if(lead){families.delete(lead[0]);const reordered=new Map([lead,...families]);families.clear();reordered.forEach((items,key)=>families.set(key,items));}}
      for (const [family, items] of families) {
        const row = document.createElement('tr'); row.append(text('th',label(family))); row.firstChild.scope = 'row';
        [items.filter(t=>t.verdict!=='connection_error').length,items.filter(t=>t.verdict==='pass').length,items.filter(t=>t.verdict==='fail').length,items.filter(t=>t.verdict==='connection_error').length].forEach(value=>row.append(text('td',value)));
        $('families').append(row);
        const passed = items.filter(t => t.verdict === 'pass');
        if (passed.length) { const group = document.createElement('details'); group.append(text('summary',label(family) + ' - ' + passed.length + ' passed')); passed.forEach(t=>testDetail(t,group,false)); $('passes').append(group); }
      }
      const failed = tests.filter(t => t.verdict === 'fail');
      if (!failed.length) $('failures').append(text('p','No tests with a usable result need attention. Review any tests that could not run.'));
      for (const [family, items] of families) {
        const flags=items.filter(t=>t.verdict==='fail'); if(!flags.length) continue;
        const group=document.createElement('details');group.append(text('summary',label(family) + ' / ' + flags.length + ' flags'));
        flags.sort((a,b)=>a.id==='PI-29'?-1:b.id==='PI-29'?1:0).forEach(t=>testDetail(t,group,true));$('failures').append(group);
      }
      managementBrief(data,tests,families,voice);
      if (notRun.length) { $('connection-section').hidden = false; notRun.forEach(t=>testDetail(t,$('connections'),false)); }
      if (!tests.some(t=>t.verdict==='pass')) $('passes').append(text('p','No passed test results were received.'));
      if(data.report_kind!=='starter_extensions') $('scope').textContent = 'Results of the tests listed above, run on ' + date + ' against this version of your agent. This is not a penetration test. Results apply only to these tests and this agent version. If you change the agent, results can change - rescan after fixes.';
      const partner = /^[a-z0-9-]{1,60}$/.test(data.partner || '') ? data.partner : 'partner';
      if (!$('business-priorities') && ['starter-100@3','starter-100@4'].includes(data.pack_version)) starterScopeBlocks($('families').closest('table') || $('families'), partner, data.pack_version);
      $('contact').href = 'mailto:info@backbond.ai?source=' + encodeURIComponent(partner + '-report') + '&subject=Full%20agent%20scan';
      clearStatus(); $('report').hidden = false;
      if(data.report_kind!=='starter_extensions') loadTargetedAddendum(match[1]);
    } catch { showError('The report could not be loaded. Try again later.'); }
  }
  load();
})();
