"""Build the management-first viewer from the last verified production renderer."""
from pathlib import Path
root = Path(__file__).resolve().parent
source = (root / 'report-baseline.js').read_text(encoding='utf-8')
def replace(old, new):
    global source
    assert source.count(old) == 1, old[:90]
    source = source.replace(old, new)
replace("const label = family =>", r"""Object.assign(names, {
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
  const label = family =>""")
replace("parent.append(article);", "const detail=document.createElement('details'); detail.className='test-evidence'; detail.append(text('summary',(test.id || 'Test') + ' / ' + (test.verdict==='pass' ? 'met criteria' : test.verdict==='fail' ? 'needs review' : 'unavailable')),article); parent.append(detail);")
replace("const section = document.createElement('section'); section.id = 'targeted-follow-up';", "const section = document.createElement('details'); section.id = 'targeted-follow-up'; section.append(text('summary','Separate targeted follow-up / 60 executions'));")
replace("const heading = text('h2','Original 100-test Starter scan'); $('report').prepend(heading);", "// Keep the separate addendum closed beneath the management brief.")
replace("document.title = (data.agent_label || 'Your agent') + ' - QA scan report | BackBond';", "document.title = (data.agent_label || 'Your agent') + ' - Agent assessment | BackBond';")
replace("const summary = document.createElement('p'); $('test-count').before(summary);", "const summary = $('method-summary') || document.createElement('p'); if (!$('method-summary')) $('test-count').before(summary);")
replace("} else { summary.remove(); }", "} else { summary.textContent=''; if (!$('method-summary')) summary.remove(); }")
replace("failed.forEach(t => testDetail(t,$('failures'),true));", """for (const [family, items] of families) {
        const flags=items.filter(t=>t.verdict==='fail'); if(!flags.length) continue;
        const group=document.createElement('details');group.append(text('summary',label(family) + ' / ' + flags.length + ' flags'));
        flags.forEach(t=>testDetail(t,group,true));$('failures').append(group);
      }
      managementBrief(data,tests,families,voice);""")
replace("if (['starter-100@3','starter-100@4'].includes(data.pack_version)) starterScopeBlocks", "if (!$('business-priorities') && ['starter-100@3','starter-100@4'].includes(data.pack_version)) starterScopeBlocks")
replace("  async function load() {", (root/'combined-viewer.js').read_text(encoding='utf-8')+'\n  async function load() {')
replace("const label = family => names[family] || String(family || 'Other').replaceAll('_', ' ');", "const label = family => String(family||'').includes('::') ? (PACK_NAMES[String(family).split('::')[0]]||'Test set')+' / '+label(String(family).split('::')[1]) : names[family] || String(family || 'Other').replaceAll('_', ' ');")
replace("const tests = data.tests.map", "if(data.report_kind==='starter_extensions') data.tests=combinedTests(data);\n      const tests = data.tests.map")
replace("guidance[test.family] ||", "guidance[test.source_family || test.family] ||")
replace("      for (const [family, items] of families) {\n        const row", "      if(data.report_kind==='starter_extensions'){const lead=[...families].find(([,items])=>items.some(t=>t.id==='PI-29')); if(lead){families.delete(lead[0]);const reordered=new Map([lead,...families]);families.clear();reordered.forEach((items,key)=>families.set(key,items));}}\n      for (const [family, items] of families) {\n        const row")
replace("flags.forEach(t=>testDetail(t,group,true));", "flags.sort((a,b)=>a.id==='PI-29'?-1:b.id==='PI-29'?1:0).forEach(t=>testDetail(t,group,true));")
replace("      loadTargetedAddendum(match[1]);", "      if(data.report_kind!=='starter_extensions') loadTargetedAddendum(match[1]);")
replace("      $('scope').textContent = 'Results of the tests listed above", "      if(data.report_kind!=='starter_extensions') $('scope').textContent = 'Results of the tests listed above")
# BEAAA-39654: the served HTML carries no loading placeholder. The status
# element starts hidden and empty; errors fill and reveal it, a successful
# render removes it together with any server-rendered first-response summary.
replace("  async function load() {", """  function clearPrerender() { const pre = $('prerender-summary'); if (pre) pre.remove(); }
  function showError(message) { clearPrerender(); const status = $('report-error'); if (!status) return; status.textContent = message; status.hidden = false; }
  function clearStatus() { clearPrerender(); const status = $('report-error'); if (status) status.remove(); }
  async function load() {""")
replace("if (!match) { $('report-error').textContent = 'This report link is invalid. Open the complete link from your report email.'; return; }", "if (!match) { showError('This report link is invalid. Open the complete link from your report email.'); return; }")
replace("else $('report-error').textContent = response.status", "else showError(response.status")
replace("'The report service is unavailable. Try again later.'; return; }", "'The report service is unavailable. Try again later.'); return; }")
replace("} catch { $('report-error').textContent = 'The report could not be loaded. Try again later.'; }", "} catch { showError('The report could not be loaded. Try again later.'); }")
replace("$('report-error').replaceChildren(); $('report-error').hidden = true; $('report').hidden = false;", "clearStatus(); $('report').hidden = false;")
assert "report-error').textContent" not in source and 'Loading your report' not in source
(root / 'scan/report.js').write_text(source, encoding='utf-8', newline='\n')
print('Built management-first viewer; frozen voice roster and not-run semantics retained.')
