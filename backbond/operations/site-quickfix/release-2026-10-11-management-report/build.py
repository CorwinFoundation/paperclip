"""Build the management-first viewer from the last verified production renderer."""
from pathlib import Path
root = Path(__file__).resolve().parent
source = (root / 'report-baseline.js').read_text(encoding='utf-8')
def replace(old, new):
    global source
    assert source.count(old) == 1, old[:90]
    source = source.replace(old, new)
# The rebased baseline owns layout, report copy, and recipient verification.
# Apply only evidence additions that are absent from that baseline.
replace("    const detail=document.createElement('details');", """    const refs = [
      ['Transcript', test.transcript_uri || test.transcript_url],
      ['Tool-call log', test.tool_call_log_uri || test.tool_call_log_url],
      ['Evidence artifact', test.evidence_uri || test.evidence_url],
      ...(Array.isArray(test.evidence_refs) ? test.evidence_refs.map((ref,index) => {
        if (typeof ref === 'string') return ['Evidence ' + (index + 1), ref];
        if (!ref || typeof ref !== 'object') return ['', ''];
        const kind=String(ref.kind || 'Evidence').replaceAll('_',' ');
        return [kind.charAt(0).toUpperCase()+kind.slice(1), ref.artifact_uri || ref.uri || ref.url];
      }) : [])
    ].filter(([,uri]) => typeof uri === 'string' && /^https:\\/\\//i.test(uri));
    if (refs.length) {
      const links=document.createElement('p'); links.className='evidence-links';
      refs.forEach(([label,uri])=>{const link=text('a',label);link.href=uri;link.rel='noopener noreferrer';links.append(link);});
      article.append(links);
    }
    const detail=document.createElement('details');""")
# Combined reports retain the explicit no-score boundary. The rebased renderer
# also supports optional annotations absent from the generic report template.
replace("  function combinedBrief(data,tests,families) {", """  function combinedBrief(data,tests,families) {
    if(data.full_scan_score!==null || data.starter_score!==null || data.underwriting_active!==false || data.priced_score_weight!==0 || !Array.isArray(data.uncovered_dimensions) || data.uncovered_dimensions.length!==8) throw new Error('combined scoring boundary');""")
for element in ('exposure-boundary', 'findings-columns', 'product-context', 'secondary-findings', 'secondary-findings-text'):
    for line in source.splitlines():
        if line.lstrip().startswith("$('" + element + "')."):
            replace(line, "    if ($('" + element + "')) " + line.strip())
replace("  async function load() {", """  function renderEvidenceRecord(data, when) {
    const list=$('evidence-provenance'); if(!list) return;
    const models=data.model_versions;
    const readable=value => value && typeof value==='object' ? JSON.stringify(value) : String(value);
    const modelText=models && typeof models==='object' ? Object.entries(models).map(([k,v])=>k+': '+readable(v)).join('; ') : null;
    const rows=[['Scanner version',data.scanner_version],['Test corpus',data.corpus_version || data.pack_version],['Model versions',modelText],['Seed',data.seed],['Run date',when.toISOString()]];
    rows.forEach(([label,value])=>{const wrap=document.createElement('div');wrap.append(text('dt',label),text('dd',value===undefined||value===null||value===''?'Not recorded in this report':value));list.append(wrap);});
    const days=Math.max(0,Math.floor((Date.now()-when.getTime())/86400000));
    $('evidence-age').textContent='Evidence age: '+days+' day'+(days===1?'':'s')+' old. Rescan after material agent changes.';
  }
  async function load() {""")
replace("      $('agent-name').textContent = data.agent_label || 'Your agent';", "      renderEvidenceRecord(data, when);\n      $('agent-name').textContent = data.agent_label || 'Your agent';")
assert "report-error').textContent" not in source and 'Loading your report' not in source
(root / 'scan/report.js').write_text(source, encoding='utf-8', newline='\n')
print('Built management-first viewer; frozen voice roster and not-run semantics retained.')
